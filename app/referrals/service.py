import re
import uuid
import json
from app.common.logging import get_logger
from datetime import datetime, timezone
from typing import List, Optional
import asyncio
import random
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals.models import (
    Referral,
    ReferralStatus,
    is_valid_referral_transition,
)
from app.referrals.repository import ReferralRepository
from app.referrals.schemas import (
    UpdateReferralRequest,
    GenerateReferralsResponse,
    ReferralResponse,
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


def _render_stored_query(query: str, company: str) -> str:
    """Render a stored referral query with the runtime company name."""
    rendered = (query or "").strip()
    if not rendered:
        return rendered

    if "company_name" in rendered.lower():
        return re.sub(
            r"\bcompany_name\b",
            company,
            rendered,
            flags=re.IGNORECASE,
        )

    return rendered


def _build_referral_queries(
    company: str, extracted_department: Optional[List[str]]
) -> list[tuple[str, int]]:
    """Build LinkedIn referral search queries.
    Prefer prebuilt Google X-Ray query strings stored on the JD
    (extracted_department). If none are available, fall back to a single
    company-scoped query so referrals can still be generated.
    """

    search_query = (
        f'site:linkedin.com/in "{company}" '
        f'("Engineering Lead" OR "Manager" OR "Tech Manager" OR "VP Engineering" OR "Backend Lead")'
    )

    extracted_dept = [
        q.strip()
        for q in (extracted_department or [])
        if str(q or "").strip()
    ]

    if extracted_dept:
        # TODO: Come back to llm generation in future
        # return [
        #     (_render_stored_query(query, company), index + 1)
        #     for index, query in enumerate(queries[:10])
        # ]

        formatted_depts = [f'"{dept}"' for dept in extracted_dept]
        dept_clause = f"({' OR '.join(formatted_depts)})"
        search_query += f" AND {dept_clause}"

    search_query += " AND India"
    return [(search_query, 1)]


def _normalize_linkedin_url(url: str) -> str:
    """Normalize a LinkedIn profile URL to the canonical www form with a trailing slash.

    Strips country-code / locale subdomains (in.linkedin.com, uk.linkedin.com, …)
    and ensures a single trailing slash, so the same profile is never stored twice
    under different host variants.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()

    host = parsed.netloc.lower()
    if host.endswith("linkedin.com"):
        host = "www.linkedin.com"

    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path = path + "/"

    return urlunparse(("https", host, path, "", "", ""))


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
        name = (
            title.split(" - ")[0].strip()
            if " - " in title
            else title.strip()
        )
        # Reject obvious non-person titles
        if not name or any(
            bad in name.lower()
            for bad in ("job", "posting", "position", "opening")
        ):
            continue
        candidates.append(
            {
                "name": name,
                "linkedin_url": _normalize_linkedin_url(link),
            }
        )
    return candidates


class ReferralService:
    def __init__(self, db: AsyncSession):
        self.db = db
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

        queries = _build_referral_queries(company, extracted_department)
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
        ddgs_client = DDGS()

        MAX_REFERRALS = 10

        for index, (query, priority) in enumerate(queries):
            if len(candidates) >= MAX_REFERRALS:
                break
            try:
                items = list(
                    ddgs_client.text(query=query, max_results=10) or []
                )
                logger.info(
                    "ddgs_results query_index=%d priority=%d results=%d",
                    index,
                    priority,
                    len(items),
                )
                for c in _extract_linkedin_candidates(items):
                    if c["linkedin_url"] not in seen_urls:
                        seen_urls.add(c["linkedin_url"])
                        candidates.append({**c, "priority": priority})
                        if len(candidates) >= MAX_REFERRALS:
                            break
            except Exception as e:
                logger.error(
                    "ddgs_query_failed index=%d query=%s error=%s",
                    index,
                    query,
                    str(e),
                )
                continue

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
        """List referrals for a job, sorted by the requested column.

        Defaults to priority ascending (most referable first). `order_by` and
        `descending` are passed through to the repository so callers can sort
        by any Referral column (e.g. "name", "created_at") without breaking
        existing callers.
        """
        return await self.repo.list_by_job(
            job_id, order_by=order_by, descending=descending
        )

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
            updates["linkedin_url"] = _normalize_linkedin_url(
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

        return await self.repo.update(referral, **updates)

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
