import html as _html_mod
import json as _json
import re
import uuid
from app.common.logging import get_logger

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.job_jd.models import JobJD
from app.job_jd.repository import JobJDRepository
from app.job_jd.schemas import UpdateJDRequest
from app.llm.schemas import JobParseSchema
from app.users.models import User
from app.llm.client import LLMClient
from app.llm.prompts import JD_PARSE_SYSTEM, JD_PARSE_USER
from app.common.exceptions import (
    NotFoundError,
    BadRequestError,
    ExternalServiceError,
)
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


def _as_str_list(value) -> list[str]:
    """Normalize a JSON-LD field that may be a string, list[str], or list of
    StructuredValue dicts into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [
            v.strip() for v in re.split(r"[;,]", value) if v.strip()
        ]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str):
                out.append(v.strip())
            elif isinstance(v, dict):
                name = v.get("name") or v.get("value")
                if name:
                    out.append(str(name).strip())
        return [v for v in out if v]
    return []


def _extract_jsonld_meta(soup: BeautifulSoup) -> tuple[str, dict]:
    """Extract job description and structured metadata from JSON-LD JobPosting.

    Workday and similar SPAs embed the full JD in structured data even when the
    visible page body is empty (JS-rendered). This is our primary text source
    AND our primary metadata source in non-AI mode: it reliably yields company,
    role, workday id, skills, keywords, and team signals without any regex.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
        except (_json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") != "JobPosting":
            continue
        description = _html_mod.unescape(data.get("description", ""))
        if not description:
            continue

        meta: dict = {}

        if data.get("title"):
            meta["role"] = data["title"]

        org = data.get("hiringOrganization")
        if isinstance(org, str) and org.strip():
            meta["company"] = org.strip()
        elif isinstance(org, dict) and org.get("name"):
            # Strip leading numeric tenant IDs like "8297 Sandvik Mining..."
            name = org["name"].strip()
            if name and name[0].isdigit():
                parts = name.split(" ", 1)
                if len(parts) == 2:
                    name = parts[1]
            meta["company"] = name

        identifier = data.get("identifier")
        if isinstance(identifier, str) and identifier.strip():
            meta["workday_job_id"] = identifier.strip()
        elif isinstance(identifier, dict) and identifier.get("value"):
            meta["workday_job_id"] = str(identifier["value"])

        skills = _as_str_list(data.get("skills"))
        if skills:
            meta["skills"] = {"required": skills, "preferred": []}

        keywords = _as_str_list(data.get("keywords"))
        if keywords:
            meta["keywords"] = keywords

        # Team signals from industry + employment type + skills-as-tech-stack.
        industry = data.get("industry")
        if isinstance(industry, list):
            industry = industry[0] if industry else None

        if skills or (isinstance(industry, str) and industry):
            meta["team_signals"] = {
                "team_size": None,
                "tech_stack": skills,
                "industry": industry
                if isinstance(industry, str)
                else None,
            }

        return description, meta
    return "", {}


def _extract_og_description(soup: BeautifulSoup) -> str:
    tag = soup.find("meta", {"property": "og:description"})
    if tag and tag.get("content"):
        return _html_mod.unescape(tag["content"])
    return ""


async def fetch_jd_html(url: str) -> tuple[str, str, dict]:
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True
    ) as client:
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
            raise BadRequestError(
                f"Failed to fetch job URL: HTTP {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise ExternalServiceError("JD Fetch", str(e))

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    # JSON-LD must be read before script tags are decomposed
    jsonld_text, extracted_meta = _extract_jsonld_meta(soup)
    og_text = _extract_og_description(soup)

    # Natural full-text extraction: strip non-content tags, keep everything else.
    # Robust across Workday SPAs, LinkedIn, and classic job boards.
    text_soup = BeautifulSoup(html, "html.parser")
    for tag in text_soup(
        ["script", "style", "header", "footer", "nav"]
    ):
        tag.decompose()
    visible_text = "\n".join(
        ln.strip()
        for ln in text_soup.get_text(separator="\n").splitlines()
        if ln.strip()
    )

    # Priority: JSON-LD > og:description > visible text > raw HTML (last resort)
    if jsonld_text:
        logger.info(
            "jd_source=jsonld extracted_meta_keys=%s",
            list(extracted_meta.keys()),
        )
        return html, jsonld_text, extracted_meta

    if og_text:
        logger.info("jd_source=og_description")
        return html, og_text, {}

    if visible_text:
        logger.info("jd_source=visible_text len=%d", len(visible_text))
        return html, visible_text, {}

    # Last resort: return the script-stripped HTML so the LLM still has page content.
    logger.warning("jd_source=raw_html_fallback url=%s", url)
    return html, text_soup.prettify(), {}


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
        ai: bool = True,
    ) -> JobJD:
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            logger.info(
                "[ERROR]: LLM provider and API key must be configured in your profile"
            )
            raise BadRequestError(
                "LLM provider and API key must be configured in your profile"
            )

        logger.info(
            "jd_fetch_start job_id=%s url=%s ai=%s",
            str(job_id),
            workday_url,
            ai,
        )
        raw_html, raw_text, extracted_meta = await fetch_jd_html(
            workday_url
        )
        logger.info(
            "jd_fetched job_id=%s text_len=%s source_keys=%s",
            str(job_id),
            len(raw_text),
            list(extracted_meta.keys()),
        )

        if _is_job_closed(raw_text):
            raise BadRequestError(
                "This job posting is no longer available or has been closed"
            )

        if not raw_text.strip():
            logger.warning(
                "jd_text_empty job_id=%s url=%s",
                str(job_id),
                workday_url,
            )
            raise BadRequestError(
                "Failed to extract job text from the page. The page may require JavaScript rendering."
            )

        if not ai:
            parsed = {
                "company": extracted_meta.get("company"),
                "role": extracted_meta.get("role"),
                "workday_job_id": extracted_meta.get("workday_job_id"),
                "skills": extracted_meta.get("skills"),
                "keywords": extracted_meta.get("keywords"),
                "team_signals": extracted_meta.get("team_signals"),
                "llm_summary": extracted_meta.get("llm_summary"),
            }

            logger.info("parsed metadata %s", parsed)
            jd = await self.repo.upsert(
                job_id=job_id,
                raw_html=raw_html[:50000],
                raw_text=raw_text[:20000],
                company=parsed["company"],
                role=parsed["role"],
                workday_job_id=parsed["workday_job_id"],
                skills=parsed["skills"],
                keywords=parsed["keywords"],
                team_signals=parsed["team_signals"],
                llm_summary="Do AI parse to get llm summary",
            )
            logger.info(
                "jd_parsed job_id=%s ai=False company=%s role=%s",
                str(job_id),
                parsed.get("company"),
                parsed.get("role"),
            )
            return jd, parsed

        # AI mode: one combined LLM call extracts fields + interview-prep learning.
        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        prompt = JD_PARSE_USER.format(raw_text=raw_text[:12000])
        logger.info("jd_llm_parse_start job_id=%s", str(job_id))
        parsed = await llm.complete_json(
            system=JD_PARSE_SYSTEM,
            user=prompt,
            model=user.current_llm_model,
            response_schema=JobParseSchema,
            max_tokens=8192,
        )
        logger.info("jd_llm_parsed job_id=%s", str(job_id))

        jd = await self.repo.upsert(
            job_id=job_id,
            raw_html=raw_html[:50000],
            raw_text=raw_text[:20000],
            company=parsed.get("company"),
            role=parsed.get("role"),
            workday_job_id=parsed.get("workday_job_id"),
            skills=parsed.get("skills"),
            keywords=parsed.get("keywords"),
            team_signals=parsed.get("team_signals"),
            llm_summary=parsed.get("llm_summary"),
            learning=parsed.get("learning"),
        )
        logger.info(
            "jd_parsed job_id=%s ai=True has_learning=%s",
            str(job_id),
            bool(parsed.get("learning")),
        )
        return jd, parsed

    async def update(
        self,
        job_id: uuid.UUID,
        data: UpdateJDRequest,
    ) -> JobJD:
        """Apply a partial update to an existing JD."""
        jd = await self.get_by_job_id(job_id)

        # Build a dict of only the fields the client sent. Pydantic v2 exposes
        # `model_fields_set` so we update exactly what was provided, leaving
        # omitted fields (even if they were None) unchanged.
        updates = {
            k: v
            for k, v in data.model_dump().items()
            if k in data.model_fields_set
        }
        if not updates:
            logger.info("jd_update_noop job_id=%s", str(job_id))
            return jd

        jd = await self.repo.update(jd, **updates)
        logger.info(
            "jd_updated job_id=%s fields=%s",
            str(job_id),
            list(updates.keys()),
        )
        return jd
