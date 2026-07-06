import uuid
from app.common.logging import get_logger
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.job_jd.models import JobJD
from app.job_jd.repository import JobJDRepository
from app.job_jd.schemas import JobJDResponse
from app.users.models import User
from app.llm.client import LLMClient
from app.llm.prompts import JD_PARSE_SYSTEM, JD_PARSE_USER
from app.common.exceptions import NotFoundError, BadRequestError, ExternalServiceError
from app.users.service import UserService

logger = get_logger(__name__)

JD_CLOSED_SIGNALS = [
    "job is no longer available",
    "this job has expired",
    "position has been filled",
    "no longer accepting applications",
    "this position is closed",
    "job posting has been removed",
]


def _is_job_closed(text: str) -> bool:
    lower = text.lower()
    return any(signal in lower for signal in JD_CLOSED_SIGNALS)


async def fetch_jd_html(url: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            response = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise BadRequestError(f"Failed to fetch job URL: HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise ExternalServiceError("JD Fetch", str(e))

    html = response.text
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return html, text


class JobJDService:
    def __init__(self, db: AsyncSession):
        self.repo = JobJDRepository(db)

    async def get_by_job_id(self, job_id: uuid.UUID) -> JobJD:
        jd = await self.repo.get_by_job_id(job_id)
        if jd is None:
            raise NotFoundError("JobJD", str(job_id))
        return jd

    async def parse_and_store(
        self,
        job_id: uuid.UUID,
        workday_url: str,
        user: User,
    ) -> JobJD:
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError("LLM provider and API key must be configured in your profile")

        logger.info("jd_fetch_start job_id=%s url=%s", str(job_id), workday_url)
        raw_html, raw_text = await fetch_jd_html(workday_url)

        if _is_job_closed(raw_text):
            raise BadRequestError("This job posting is no longer available or has been closed")

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        prompt = JD_PARSE_USER.format(raw_text=raw_text[:12000])

        logger.info("jd_llm_parse_start job_id=%s", str(job_id))
        parsed = await llm.complete_json(system=JD_PARSE_SYSTEM, user=prompt)

        jd = await self.repo.upsert(
            job_id=job_id,
            raw_html=raw_html[:50000],
            raw_text=raw_text[:20000],
            skills=parsed.get("skills"),
            keywords=parsed.get("keywords"),
            team_signals=parsed.get("team_signals"),
            llm_summary=parsed.get("llm_summary"),
        )
        logger.info("jd_parsed job_id=%s", str(job_id))
        return jd, parsed
