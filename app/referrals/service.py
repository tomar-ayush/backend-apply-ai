import uuid
import json
from app.common.logging import get_logger
from datetime import datetime, timezone
from typing import List, Optional
import asyncio
import random
from urllib.parse import urlparse, urlunparse

import httpx
import http.client
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.common.events import event_bus
from app.common.state_machine import StateMachine
from app.common.validators import normalize_linkedin_url
from app.referrals.models import (
    Referral,
    ReferralStatus,
    is_valid_referral_transition,
    referral_state_machine,
)
from app.referrals.repository import ReferralRepository
from app.referrals.schemas import (
    UpdateReferralRequest,
    GenerateReferralsResponse,
    ReferralResponse,
    CreateReferralRequest,
)
from app.jobs.models import Job
from app.users.models import User
from app.job_jd.repository import JobJDRepository
from app.jobs.repository import JobRepository
from app.common.exceptions import (
    NotFoundError,
    InvalidTransitionError,
    BadRequestError,
    ForbiddenError,
)
from app.config import settings

from ddgs import DDGS

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Search helpers (pure functions, no state)
# ---------------------------------------------------------------------------


def _render_stored_query(query: str, company: str) -> str:
    """Return a stored referral query as-is.

    The `company_name` token is already substituted at JD parse time
    (in JobJDService.parse_and_store), so no runtime rendering is needed.
    `company` is accepted for signature compatibility but unused.
    """
    return (query or "").strip()


def _build_referral_queries(
    company: str,
    extracted_department: Optional[List[str]],
    role: Optional[str] = None,
) -> list[tuple[str, int]]:
    """Build LinkedIn referral search queries from prebuilt Google X-Ray
    query strings stored on the JD (extracted_department).

    Each stored query is rendered with the runtime company name (the literal
    `company_name` token is substituted) and assigned a priority based on its
    position. If no stored queries exist, fall back to a company + role scoped
    query so referrals can still be generated.
    """
    queries = [
        q.strip()
        for q in (extracted_department or [])
        if str(q or "").strip()
    ]
    if queries:
        return [
            (_render_stored_query(query, company), index + 1)
            for index, query in enumerate(queries[:10])
        ]

    role_kw = ""
    if role:
        words = [w for w in role.lower().split() if len(w) > 3][:2]
        if words:
            role_kw = " ".join(words)
    if role_kw:
        fallback = (
            f'site:linkedin.com/in "{company}" '
            f'("{role_kw} lead" OR "{role_kw} manager" OR "head of {role_kw}") '
            f"AND India"
        )
    else:
        fallback = (
            f'site:linkedin.com/in "{company}" '
            f'("Engineering Lead" OR "Manager" OR "Tech Manager" '
            f'OR "VP Engineering" OR "Backend Lead") AND India'
        )
    return [(fallback, 1)]


def _extract_linkedin_candidates(items) -> list[dict]:
    """Parse DDG search results into candidate dicts.

    DDG returns 'href' (not 'link') for the URL.
    Titles follow the pattern: "Full Name - Title - Company | LinkedIn"
    """
    candidates = []
    for item in items or []:
        link = item.get("href") or item.get("link", "")
        if "linkedin.com/in/" not in link:
            continue
        title = item.get("title", "")
        name = (
            title.split(" - ")[0].strip()
            if " - " in title
            else title.strip()
        )
        if not name or any(
            bad in name.lower()
            for bad in ("job", "posting", "position", "opening")
        ):
            continue
        candidates.append(
            {
                "name": name,
                "linkedin_url": normalize_linkedin_url(link),
            }
        )
    return candidates


def _search_serper(query: str, max_results: int = 10) -> list[dict]:
    """Search Google via the Serper API and return DDG-shaped result items."""
    if not settings.SERPER_API_KEY:
        logger.warning(
            "serper_skipped query=%s reason=SERPER_API_KEY not configured",
            query,
        )
        return []
    try:
        conn = http.client.HTTPSConnection(
            "google.serper.dev", timeout=30
        )
        payload = json.dumps({"q": query})
        headers = {
            "X-API-KEY": settings.SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        conn.request("POST", "/search", payload, headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
    except Exception as e:
        logger.error(
            "serper_query_failed query=%s error=%s", query, str(e)
        )
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    items: list[dict] = []
    for result in (
        data.get("organic") or data.get("organicResults") or []
    ):
        link = result.get("link") or ""
        if not link:
            continue
        items.append({"href": link, "title": result.get("title", "")})
    return items


def _search_linkedin_candidates(
    query: str, max_results: int = 10
) -> list[dict]:
    """Search for LinkedIn profiles, preferring Serper (Google) with DDGS fallback."""
    logger.info("query passing to serper: %s", query)
    items = _search_serper(query, max_results)
    if not items:
        logger.warning(
            "serper_empty_fallback query=%s reason=serper returned no items, using DDGS",
            query,
        )
        try:
            items = list(
                DDGS().text(query=query, max_results=max_results) or []
            )
        except Exception as e:
            logger.error(
                "ddgs_query_failed query=%s error=%s", query, str(e)
            )
            return []
    return _extract_linkedin_candidates(items)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class _DedupFilter:
    """Tracks referral identity keys (name + normalized URL) for a job so the
    same person is never stored twice — across the DB and within a single run.
    """

    def __init__(self, existing: List[Referral]):
        self.names = {
            (r.name or "").strip().lower() for r in existing if r.name
        }
        self.urls = {r.linkedin_url for r in existing if r.linkedin_url}

    def is_duplicate(self, name: str, url: Optional[str]) -> bool:
        name = (name or "").strip()
        if name and name.lower() in self.names:
            return True
        if url and url in self.urls:
            return True
        return False

    def add(self, name: str, url: Optional[str]) -> None:
        name = (name or "").strip()
        if name:
            self.names.add(name.lower())
        if url:
            self.urls.add(url)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReferralService(BaseService):
    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.repo = ReferralRepository(db)

    async def generate(
        self, job: Job, user: User
    ) -> GenerateReferralsResponse:
        jd_repo = JobJDRepository(self.db)
        jd = await jd_repo.get_by_job_id(job.id)
        if jd is None:
            raise BadRequestError(
                "Job JD must be parsed before generating referrals"
            )

        company = jd.company
        if not company:
            raise BadRequestError(
                "Company name must be available in the job record to generate referrals"
            )

        role = jd.role
        extracted_department = jd.extracted_department or []

        dedup = _DedupFilter(await self.repo.list_for_job(job.id))

        queries = _build_referral_queries(
            company, extracted_department, role
        )
        logger.info(
            "referral_generation_start job_id=%s company=%s role=%s queries=%d",
            str(job.id),
            company,
            role,
            len(queries),
        )
        logger.info(
            "referral_queries job_id=%s queries=%s",
            str(job.id),
            [q for q, _ in queries],
        )

        candidates: list[dict] = []
        seen_urls: set[str] = set()

        MAX_REFERRALS = 10

        for index, (query, priority) in enumerate(queries):
            if len(candidates) >= MAX_REFERRALS:
                break
            found = _search_linkedin_candidates(query, max_results=10)
            logger.info(
                "search_results query_index=%d priority=%d results=%d",
                index,
                priority,
                len(found),
            )
            for c in found:
                url = c["linkedin_url"]
                name = (c.get("name") or "").strip()
                if url in seen_urls or dedup.is_duplicate(name, url):
                    continue
                seen_urls.add(url)
                dedup.add(name, url)
                candidates.append({**c, "priority": priority})
                if len(candidates) >= MAX_REFERRALS:
                    break

            if index < len(queries) - 1:
                await asyncio.sleep(random.uniform(2.5, 6.5))

        logger.info(
            "referral_candidates_found job_id=%s count=%d",
            str(job.id),
            len(candidates),
        )

        if not candidates:
            logger.warning(
                "no_referral_candidates job_id=%s", str(job.id)
            )
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
        logger.info(
            "referrals_generated job_id=%s count=%s",
            str(job.id),
            len(referrals),
        )

        await event_bus.publish(
            f"user:{user.id}",
            {
                "type": "referrals_generated",
                "job_id": str(job.id),
                "count": len(referrals),
            },
        )

        return GenerateReferralsResponse(
            generated=len(referrals),
            referrals=[
                ReferralResponse.model_validate(r) for r in referrals
            ],
        )

    async def list_by_job(
        self,
        job_id: uuid.UUID,
        order_by: Optional[str] = "priority",
        descending: bool = False,
    ) -> List[Referral]:
        return await self.repo.list_by_job(
            job_id, order_by=order_by, descending=descending
        )

    async def create_many_referrals(
        self,
        job: Job,
        referrals: List[CreateReferralRequest],
    ) -> List[Referral]:
        """Persist a list of user-provided referrals for a job."""
        dedup = _DedupFilter(await self.repo.list_for_job(job.id))

        records = []
        skipped = 0
        for r in referrals:
            name = (r.name or "").strip()
            url = (
                normalize_linkedin_url(r.linkedin_url)
                if r.linkedin_url
                else None
            )
            if dedup.is_duplicate(name, url):
                skipped += 1
                continue
            records.append(
                {
                    "job_id": job.id,
                    "name": name,
                    "linkedin_url": url,
                    "priority": r.priority,
                }
            )
            dedup.add(name, url)

        if not records:
            logger.info(
                "referrals_created_manually job_id=%s count=0 skipped=%d",
                str(job.id),
                skipped,
            )
            return []

        created = await self.repo.create_many(records)
        logger.info(
            "referrals_created_manually job_id=%s count=%s skipped=%d",
            str(job.id),
            len(created),
            skipped,
        )
        return created

    async def _get_and_assert_ownership(
        self, referral_id: uuid.UUID, user: User
    ) -> Referral:
        referral = await self.repo.get_by_id(referral_id)
        if referral is None:
            raise NotFoundError("Referral", str(referral_id))
        job = await JobRepository(self.db).get_by_id(referral.job_id)
        if job is None or job.user_id != user.id:
            raise ForbiddenError(
                "You do not have access to this referral"
            )
        return referral

    async def update(
        self,
        referral_id: uuid.UUID,
        req: UpdateReferralRequest,
        user: User,
    ) -> Referral:
        referral = await self._get_and_assert_ownership(
            referral_id, user
        )

        if not is_valid_referral_transition(
            referral.status, req.status
        ):
            raise InvalidTransitionError(
                referral.status.value, req.status.value
            )

        updates: dict = {"status": req.status}
        if req.linkedin_url is not None:
            updates["linkedin_url"] = normalize_linkedin_url(
                req.linkedin_url
            )

        now = datetime.now(timezone.utc)
        if req.status == ReferralStatus.REQUESTED:
            updates["asked_at"] = now
        elif req.status in (
            ReferralStatus.RESPONDED,
            ReferralStatus.REFERRED,
            ReferralStatus.DECLINED,
        ):
            updates["responded_at"] = now

        updated = await self.repo.update(referral, **updates)
        logger.info(
            "referral_updated referral_id=%s status=%s",
            str(referral_id),
            req.status.value,
        )

        await event_bus.publish(
            f"user:{user.id}",
            {
                "type": "referral_updated",
                "referral_id": str(referral_id),
                "job_id": str(updated.job_id),
                "status": req.status.value,
            },
        )
        return updated

    async def delete(self, referral_id: uuid.UUID, user: User) -> None:
        referral = await self._get_and_assert_ownership(
            referral_id, user
        )
        await self.repo.delete(referral)
        logger.info(
            "referral_deleted referral_id=%s user_id=%s",
            str(referral_id),
            str(user.id),
        )

        await event_bus.publish(
            f"user:{user.id}",
            {
                "type": "referral_deleted",
                "referral_id": str(referral_id),
                "job_id": str(referral.job_id),
            },
        )
