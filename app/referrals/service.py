import re
import uuid
import json
from app.common.logging import get_logger
from datetime import datetime, timezone
from typing import List
import asyncio
import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals.models import Referral, ReferralStatus, is_valid_referral_transition
from app.referrals.repository import ReferralRepository
from app.referrals.schemas import UpdateReferralRequest, GenerateReferralsResponse, ReferralResponse, ReferralSearchSchema
from app.jobs.models import Job
from app.users.models import User
from app.job_jd.repository import JobJDRepository
from app.llm.client import LLMClient
from app.llm.prompts import REFERRAL_SEARCH_SYSTEM, REFERRAL_SEARCH_USER
from app.common.exceptions import NotFoundError, InvalidTransitionError, BadRequestError, ForbiddenError

from ddgs import DDGS

logger = get_logger(__name__)


def _shorten_company_name(company: str) -> str:
    """Return the shortest searchable form — strips legal suffixes and numeric tenant prefixes."""
    suffixes = sorted([
        " pvt ltd", " pvt. ltd.", " private limited", " limited",
        " ltd.", " ltd", " inc.", " inc", " corp.", " corp", " llc", " plc",
        " & rock technology india pvt ltd", " & rock technology india",
        " mining & rock technology india", " mining & rock solutions",
        " mining and rock solutions", " & rock solutions",
    ], key=len, reverse=True)
    name = company.strip()
    lower = name.lower()
    for suffix in suffixes:
        if lower.endswith(suffix):
            name = name[: -len(suffix)].strip()
            lower = name.lower()
            break
    parts = name.split(" ", 1)
    if len(parts) == 2 and parts[0].isdigit():
        name = parts[1]
    return name.strip()


def _clean_role(role: str) -> str:
    """Strip parenthetical abbreviations like (GET), (SDE) that hurt search recall."""
    return re.sub(r"\s*\([^)]*\)", "", role).strip()


def _build_referral_queries(company: str, role: str, team_signals: dict) -> list[tuple[str, int]]:
    """
    Build diverse LinkedIn search queries targeting current employees who could refer.

    Strategy: search for COLLEAGUES (any level), not exact-title matches.
    People with the identical trainee title can't refer you — senior engineers and
    managers can. Queries fan out across role function, department, industry, and HR.
    """
    short = _shorten_company_name(company)
    clean = _clean_role(role)
    signals = team_signals or {}
    industry = (signals.get("industry") or "").lower()
    tech: list = signals.get("tech_stack") or []

    # Extract meaningful function keywords from role, drop generic words
    _stop = {"graduate", "engineer", "trainee", "senior", "junior", "lead",
             "associate", "intern", "manager", "analyst", "specialist"}
    role_kws = [w for w in clean.lower().split() if w not in _stop and len(w) > 3]

    # list of (query, priority) — lower number = contact first
    tagged: list[tuple[str, int]] = []

    planning_signals = {"plan", "supply", "demand", "inventory", "logistics", "procurement"}
    is_planning = any(k in role.lower() for k in planning_signals)

    # --- Priority 1: Manager in the same team/function ---
    for kw in role_kws[:2]:
        tagged.append((f'site:linkedin.com/in "{short}" "{kw}" manager -jobs', 1))
    if is_planning:
        tagged.append((f'site:linkedin.com/in "{short}" "supply chain" manager -jobs', 1))
        tagged.append((f'site:linkedin.com/in "{short}" "planning" manager -jobs', 1))

    # --- Priority 2: Senior / mid-level peers in the same function ---
    for kw in role_kws[:2]:
        tagged.append((f'site:linkedin.com/in "{short}" "senior {kw}" OR "lead {kw}" -jobs', 2))
    if is_planning:
        tagged.append((f'site:linkedin.com/in "{short}" "senior" "supply chain" -jobs', 2))
        tagged.append((f'site:linkedin.com/in "{short}" "demand planning" OR "inventory planning" -jobs', 2))

    # --- Priority 3: Broader same-function pool ---
    for kw in role_kws[:2]:
        tagged.append((f'site:linkedin.com/in "{short}" "{kw}" -jobs', 3))
    tagged.append((f'site:linkedin.com/in "{short}" engineer -jobs -recruiter', 3))
    if industry and industry not in {"", "none"}:
        tagged.append((f'site:linkedin.com/in "{short}" "{industry}"', 3))
    if tech:
        tagged.append((f'site:linkedin.com/in "{short}" {tech[0]} -jobs', 3))

    # --- Priority 4: General managers / directors ---
    tagged.append((f'site:linkedin.com/in "{short}" manager -jobs -recruiter', 4))
    tagged.append((f'site:linkedin.com/in "{short}" director -jobs -recruiter', 4))

    # --- Priority 5: HR / Recruiter ---
    tagged.append((f'site:linkedin.com/in "{short}" "talent acquisition" OR recruiter', 5))

    # --- Priority 5: Broad fallback without site: ---
    tagged.append((f'linkedin.com/in "{short}" {clean} -job -"job posting"', 5))

    seen: set[str] = set()
    deduped: list[tuple[str, int]] = []
    for q, p in tagged:
        if q not in seen:
            seen.add(q)
            deduped.append((q, p))

    return deduped[:10]


def _extract_linkedin_candidates(items) -> list[dict]:
    """Parse DDG search results into candidate dicts.

    DDG returns 'href' (not 'link') for the URL.
    Titles follow the pattern: "Full Name - Title - Company | LinkedIn"
    """
    candidates = []
    for item in items or []:
        # DDG uses 'href'; Google CSE uses 'link' — support both
        link = item.get("href") or item.get("link", "")
        if "linkedin.com/in/" not in link:
            continue
        title = item.get("title", "")
        name = title.split(" - ")[0].strip() if " - " in title else title.strip()
        # Reject obvious non-person titles
        if not name or any(bad in name.lower() for bad in ("job", "posting", "position", "opening")):
            continue
        candidates.append({"name": name, "linkedin_url": link})
    return candidates


class ReferralService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ReferralRepository(db)

    async def generate(self, job: Job, user: User) -> GenerateReferralsResponse:
        jd_repo = JobJDRepository(self.db)
        jd = await jd_repo.get_by_job_id(job.id)
        if jd is None:
            raise BadRequestError("Job JD must be parsed before generating referrals")

        company = job.company
        if not company or company == "PlaceHolder":
            raise BadRequestError("Company name must be available in the job record to generate referrals")

        role = job.role or "Engineer"
        team_signals = jd.team_signals or {}

        queries = _build_referral_queries(company, role, team_signals)
        logger.info(
            "referral_generation_start job_id=%s company=%s short_company=%s role=%s queries=%d",
            str(job.id), company, _shorten_company_name(company), role, len(queries),
        )
        logger.info("referral_queries job_id=%s queries=%s", str(job.id), [q for q, _ in queries])

        candidates: list[dict] = []
        seen_urls: set[str] = set()
        ddgs_client = DDGS()

        for index, (query, priority) in enumerate(queries):
            try:
                items = list(ddgs_client.text(query=query, max_results=10) or [])
                logger.info("ddgs_results query_index=%d priority=%d results=%d", index, priority, len(items))
                for c in _extract_linkedin_candidates(items):
                    if c["linkedin_url"] not in seen_urls:
                        seen_urls.add(c["linkedin_url"])
                        candidates.append({**c, "priority": priority})
            except Exception as e:
                logger.error("ddgs_query_failed index=%d query=%s error=%s", index, query, str(e))
                continue

            if index < len(queries) - 1:
                await asyncio.sleep(random.uniform(2.5, 6.5))

        logger.info("referral_candidates_found job_id=%s count=%d", str(job.id), len(candidates))

        if not candidates:
            logger.warning("no_referral_candidates job_id=%s", str(job.id))
            return GenerateReferralsResponse(generated=0, referrals=[])

        records = [
            {
                "job_id": job.id,
                "name": c["name"],
                "linkedin_url": c["linkedin_url"],
                "priority": c.get("priority", 5),
            }
            for c in candidates
        ]
        referrals = await self.repo.create_many(records)
        logger.info("referrals_generated job_id=%s count=%s", str(job.id), len(referrals))

        return GenerateReferralsResponse(
            generated=len(referrals),
            referrals=[ReferralResponse.model_validate(r) for r in referrals],
        )

    async def list_by_job(self, job_id: uuid.UUID) -> List[Referral]:
        return await self.repo.list_by_job(job_id)

    async def update(self, referral_id: uuid.UUID, req: UpdateReferralRequest, user: User) -> Referral:
        referral = await self.repo.get_by_id(referral_id)
        if referral is None:
            raise NotFoundError("Referral", str(referral_id))

        if not is_valid_referral_transition(referral.status, req.status):
            raise InvalidTransitionError(referral.status.value, req.status.value)

        updates: dict = {"status": req.status}
        if req.linkedin_url is not None:
            updates["linkedin_url"] = req.linkedin_url

        now = datetime.now(timezone.utc)
        if req.status == ReferralStatus.REQUESTED:
            updates["asked_at"] = now
        elif req.status in (ReferralStatus.RESPONDED, ReferralStatus.REFERRED, ReferralStatus.DECLINED):
            updates["responded_at"] = now

        return await self.repo.update(referral, **updates)
