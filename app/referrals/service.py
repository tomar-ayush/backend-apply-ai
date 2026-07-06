import uuid
import json
from app.common.logging import get_logger
from datetime import datetime, timezone
from typing import List

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals.models import Referral, ReferralStatus, is_valid_referral_transition
from app.referrals.repository import ReferralRepository
from app.referrals.schemas import UpdateReferralRequest, GenerateReferralsResponse, ReferralResponse
from app.jobs.models import Job
from app.users.models import User
from app.users.service import UserService
from app.job_jd.repository import JobJDRepository
from app.llm.client import LLMClient
from app.llm.prompts import REFERRAL_SEARCH_SYSTEM, REFERRAL_SEARCH_USER
from app.common.exceptions import NotFoundError, InvalidTransitionError, BadRequestError, ForbiddenError

logger = get_logger(__name__)

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


async def _google_search(api_key: str, engine_id: str, query: str, num: int = 10) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                GOOGLE_SEARCH_URL,
                params={"key": api_key, "cx": engine_id, "q": query, "num": num},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])
        except Exception as e:
            logger.warning("google_search_failed query=%s error=%s", query, str(e))
            return []


def _extract_linkedin_candidates(items: list[dict]) -> list[dict]:
    candidates = []
    for item in items:
        link = item.get("link", "")
        if "linkedin.com/in/" not in link:
            continue
        title = item.get("title", "")
        name = title.split(" - ")[0].strip() if " - " in title else title.strip()
        if name:
            candidates.append({"name": name, "linkedin_url": link})
    return candidates


class ReferralService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ReferralRepository(db)

    async def generate(self, job: Job, user: User) -> GenerateReferralsResponse:
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        google_key = user_svc.get_decrypted_google_key(user)

        if not google_key or not user.google_search_engine_id:
            raise BadRequestError("Google Search API key and engine ID must be configured")
        if not llm_key or not user.llm_provider:
            raise BadRequestError("LLM provider and API key must be configured")

        jd_repo = JobJDRepository(self.db)
        jd = await jd_repo.get_by_job_id(job.id)
        if jd is None:
            raise BadRequestError("Job JD must be parsed before generating referrals")

        company = job.company or "the company"
        team_info = str(jd.team_signals or {})

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        prompt = REFERRAL_SEARCH_USER.format(company=company, team_or_role=job.role or team_info)
        parsed = await llm.complete_json(system=REFERRAL_SEARCH_SYSTEM, user=prompt)
        queries = parsed.get("queries", [])

        candidates: list[dict] = []
        seen_urls = set()

        for query in queries[:5]:
            items = await _google_search(google_key, user.google_search_engine_id, query)
            for c in _extract_linkedin_candidates(items):
                if c["linkedin_url"] not in seen_urls:
                    seen_urls.add(c["linkedin_url"])
                    candidates.append(c)

        if not candidates:
            logger.warning("no_referral_candidates job_id=%s", str(job.id))
            return GenerateReferralsResponse(generated=0, referrals=[])

        records = [
            {"job_id": job.id, "name": c["name"], "linkedin_url": c["linkedin_url"]}
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
