import uuid
import asyncio
import base64
import httpx
import json
import re
from typing import Optional, List

from app.common.logging import get_logger

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.jobs.repository import JobRepository
from app.job_jd.repository import JobJDRepository
from app.users.models import User
from app.users.repository import UserRepository
from app.users.service import UserService
from app.llm.client import LLMClient
from app.llm.prompts import (
    SKILLS_SECTION_SYSTEM,
    SKILLS_SECTION_USER,
    PROFESSIONAL_SUMMARY_SECTION_SYSTEM,
    PROFESSIONAL_SUMMARY_SECTION_USER,
    WORK_EXPERIENCE_SECTION_SYSTEM,
    WORK_EXPERIENCE_SECTION_USER,
    PROJECT_SECTION_SYSTEM,
    USER_PROJECT_SECTION_USER,
    EDUCATION_SECTION_SYSTEM,
    EDUCATION_SECTION_USER,
)
from app.storage.r2 import r2_storage
from app.resumes.repository import LatexPackageRepository
from app.resumes.schemas import (
    CreateResumeUploadUrlsResponse,
    GenerateAiResumeResponse,
    GetResumeDownloadResponse,
)
from app.common.exceptions import BadRequestError, NotFoundError

logger = get_logger(__name__)

RESUME_TEX_CONTENT_TYPE = "text/x-tex"
RESUME_PDF_CONTENT_TYPE = "application/pdf"
PRESIGN_EXPIRY_SECONDS = 900


class ResumeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.job_repo = JobRepository(db)
        self.jd_repo = JobJDRepository(db)

    # ------------------------------------------------------------------
    # Resume endpoints decoupled from job_id
    # ------------------------------------------------------------------

    @staticmethod
    def _resume_key(user_id: uuid.UUID, kind: str, type: str) -> str:
        """kind is 'original' or 'ai'. LaTeX source stored here."""
        if type == "tex":
            return f"resume/{user_id}/{kind}_resume.tex"
        elif type == "pdf":
            return f"resume/{user_id}/{kind}_resume.pdf"
        raise ValueError(f"Unknown resume key type: {type!r}")

    async def create_upload_url(
        self, user: User
    ) -> CreateResumeUploadUrlsResponse:
        """Mint a presigned PUT URL so the client uploads the LaTeX source of one resume copy.

        The client uploads LaTeX only; the server compiles it to PDF (see finalize_resume)
        and stores both. The canonical LaTeX URL is recorded immediately since the key
        is deterministic.
        """
        key = self._resume_key(user.id, "original")
        presigned_url = r2_storage.generate_presigned_put_url(
            key, RESUME_TEX_CONTENT_TYPE, PRESIGN_EXPIRY_SECONDS
        )
        final = r2_storage._public_url(key)

        user_repo = UserRepository(self.db)
        await user_repo.update(user, original_resume_latex_url=final)

        logger.info(
            "resume_upload_url_created user_id=%s type=%s",
            str(user.id),
            "original",
        )
        return CreateResumeUploadUrlsResponse(
            latex_presigned_url=presigned_url
        )

    async def finalize_resume(
        self, resume_type: str, user: User
    ) -> GetResumeDownloadResponse:
        """Compile the just-uploaded LaTeX to PDF via latexonline.cc and store it.

        Call this after the client PUTs the LaTeX to the presigned URL. Returns the PDF
        download URL. If compilation fails, the PDF URL is None but the LaTeX is kept.
        """
        kind = resume_type
        latex_key = self._resume_key(user.id, kind, "tex")
        latex_text = r2_storage.download_text(latex_key)

        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(
            latex_text, self.db
        )
        if pdf_bytes:
            pdf_key = self._resume_key(user.id, kind, "pdf")
            pdf_url = r2_storage.upload_bytes(
                pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
            )
        else:
            logger.warning(
                "resume_pdf_compile_failed type=%s user_id=%s",
                kind,
                str(user.id),
            )

        user_repo = UserRepository(self.db)
        await user_repo.update(user, original_resume_pdf_url=pdf_url)

        download_url = (
            r2_storage.generate_presigned_get_url(
                r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
            )
            if pdf_url
            else None
        )
        logger.info(
            "resume_finalized type=%s has_pdf=%s", kind, bool(pdf_url)
        )
        return GetResumeDownloadResponse(
            version=kind,
            download_url=download_url,
            message=(
                f"{kind} resume compiled to PDF"
                if pdf_url
                else f"{kind} LaTeX uploaded but PDF compilation failed"
            ),
        )

    async def generate_ai(
        self, job_id: uuid.UUID, sections: list[str], user: User
    ) -> GenerateAiResumeResponse:
        """Generate an ATS-friendly AI resume for a job by optimizing the requested sections.

        Pipeline:
          1. Parse the original LaTeX into JSON sections (deterministic, no LLM).
          2. Run one LLM call PER requested section in PARALLEL (each gets only its block
             + the JD).
          3. For any section whose LLM output fails validation, fall back to the ORIGINAL
             section block so the resume is always complete and compilable.
          4. Reconstruct the full LaTeX deterministically by splicing optimized blocks
             back into the original (no LLM in reconstruction -> fast, reliable, cheap).
        """
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError(
                "LLM provider and API key must be configured"
            )

        if not user.original_resume_latex_url:
            raise BadRequestError(
                "Original LaTeX resume must be uploaded before generating the AI resume"
            )

        jd = await self.jd_repo.get_by_job_id(job_id)
        if jd is None:
            raise BadRequestError(
                "Job description must be parsed before generating resume"
            )

        if not jd.raw_text or not jd.raw_text.strip():
            raise BadRequestError(
                "Job description text is empty; parse the JD before generating a resume"
            )

        latex_key = r2_storage.key_from_url(
            user.original_resume_latex_url
        )
        original_latex = r2_storage.download_text(latex_key)
        logger.info(
            "ai_resume_original_latex_len=%d", len(original_latex)
        )

        # Step 1: parse into JSON sections (deterministic).
        parsed = _parse_latex_sections(original_latex)
        parsed_keys = {k: len(v) for k, v in parsed.items()}
        logger.info(
            "ai_resume_generate_start job_id=%s user_id=%s sections=%s parsed_keys=%s",
            str(job_id),
            str(user.id),
            sections,
            parsed_keys,
        )

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)

        # Step 2: parallel LLM calls (one per requested section) + topics suggestion.
        async def _optimize(section: str):
            cfg = _SECTION_PROMPTS.get(section)
            if cfg is None:
                logger.warning(
                    "ai_resume_unknown_section job_id=%s section=%s",
                    str(job_id),
                    section,
                )
                return section, None
            block = parsed.get(section)
            if not block:
                logger.warning(
                    "ai_resume_section_not_found job_id=%s section=%s",
                    str(job_id),
                    section,
                )
                return section, None
            logger.info(
                "ai_resume_optimize_start job_id=%s section=%s block_len=%d",
                str(job_id),
                section,
                len(block),
            )
            prompt = cfg["user"].format(
                job_description=jd.raw_text,
                **{cfg["arg"]: block},
            )
            new_block = await llm.complete(
                system=cfg["system"],
                user=prompt,
                model=user.current_llm_model,
                max_tokens=8192,
            )
            logger.info(
                "ai_resume_llm_raw job_id=%s section=%s new_block_len=%d new_block_preview=%r",
                str(job_id),
                section,
                len(new_block or ""),
                (new_block or "")[:400],
            )
            # Diagnostics: if the model echoed the block unchanged, log it so a
            # no-op optimization is visible instead of silently producing an
            # identical resume.
            if new_block and new_block.strip() == block.strip():
                logger.warning(
                    "ai_resume_section_unchanged job_id=%s section=%s "
                    "(model returned identical block - check JD relevance/length)",
                    str(job_id),
                    section,
                )
            return section, new_block

        optimize_tasks = [_optimize(s) for s in sections]
        optimized_results = await asyncio.gather(
            *optimize_tasks, return_exceptions=True
        )

        # Step 3: keep optimized block only if it validates; else fall back to original.
        optimized_sections: dict[str, str] = {}
        for res in optimized_results:
            if isinstance(res, Exception):
                logger.warning(
                    "ai_resume_section_failed error=%s", str(res)
                )
                continue
            section, new_block = res
            if not new_block:
                logger.warning(
                    "ai_resume_section_empty job_id=%s section=%s",
                    str(job_id),
                    section,
                )
                continue
            if _validate_latex(new_block):
                optimized_sections[section] = new_block
            else:
                logger.warning(
                    "ai_resume_section_invalid_fallback job_id=%s section=%s",
                    str(job_id),
                    section,
                )

        # Step 4: deterministic reconstruction (no LLM).
        optimized_latex = _reconstruct_latex(
            original_latex, parsed, optimized_sections
        )

        validated = _validate_latex(optimized_latex)
        if not validated:
            logger.warning(
                "ai_resume_latex_validation_failed job_id=%s",
                str(job_id),
            )

        ai_key = self._resume_key(user.id, "ai", "tex")
        latex_url = r2_storage.upload_text(
            ai_key, optimized_latex, RESUME_TEX_CONTENT_TYPE
        )

        # Compile the optimized LaTeX to PDF via the Lambda compiler and store it too.
        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(
            optimized_latex, self.db
        )
        if pdf_bytes:
            pdf_key = self._resume_key(user.id, "ai", "pdf")
            pdf_url = r2_storage.upload_bytes(
                pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
            )
        else:
            logger.warning(
                "ai_resume_pdf_compile_failed job_id=%s", str(job_id)
            )

        user_repo = UserRepository(self.db)
        await user_repo.update(
            user,
            ai_resume_latex_url=latex_url,
            ai_resume_pdf_url=pdf_url,
        )

        download_url = (
            r2_storage.generate_presigned_get_url(
                r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
            )
            if pdf_url
            else None
        )
        logger.info(
            "ai_resume_generated job_id=%s sections=%s validated=%s has_pdf=%s",
            str(job_id),
            sections,
            validated,
            bool(pdf_url),
        )
        return GenerateAiResumeResponse(
            download_url=download_url,
            validated=validated,
        )

    async def get_download_url(
        self, user: User, version: str
    ) -> GetResumeDownloadResponse:
        """Return the presigned GET URL for the compiled PDF of a stored resume copy."""
        if version == "ai":
            pdf_url = user.ai_resume_pdf_url
        else:
            pdf_url = user.original_resume_pdf_url

        if not pdf_url:
            return GetResumeDownloadResponse(
                version=version,
                download_url=None,
                message=f"No {version} resume PDF compiled yet",
            )

        download_url = r2_storage.generate_presigned_get_url(
            r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
        )
        return GetResumeDownloadResponse(
            version=version,
            download_url=download_url,
            message=f"Use the download_url to fetch the {version} resume PDF",
        )


def _validate_latex(content: str) -> bool:
    """Validate LaTeX structurally before it reaches the real pdflatex compiler.

    pylatexenc runs in TOLERANT mode and silently swallows unbalanced braces and
    unclosed custom commands, so it cannot catch the class of error that actually
    breaks compilation (e.g. `! Argument of \\@vspace has an extra }.` caused by a
    stray/missing brace in an optimized section). We therefore do a STRICT global
    brace-balance check that mirrors what pdflatex enforces:

      - Strip line comments (`% ...`) and verbatim environments first, since `%`
        and braces inside them are not real LaTeX structure.
      - Treat `\{` and `\}` (escaped braces) as literal text, not grouping.
      - Count `{` vs `}`; they must balance exactly, and depth must never go
        negative (a `}` with no matching `{`).

    This is the cheapest reliable proxy for "will pdflatex choke on this block".
    """
    # 1. Remove verbatim environments (their contents are literal).
    text = re.sub(
        r"\\begin\{verbatim\}.*?\\end\{verbatim\}",
        "",
        content,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\begin\{lstlisting\}.*?\\end\{lstlisting\}",
        "",
        text,
        flags=re.DOTALL,
    )

    # 2. Remove % line comments (but not \% which is an escaped percent).
    #    Replace `\%` with a placeholder so the comment stripper won't eat it.
    text = text.replace("\\%", "\x00")
    text = re.sub(r"%.*", "", text)
    text = text.replace("\x00", "\\%")

    # 3. Remove escaped braces so they aren't counted as grouping.
    text = text.replace("\\{", "").replace("\\}", "")

    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                logger.warning(
                    "latex_validation_error negative_depth (stray '}')"
                )
                return False
    if depth != 0:
        logger.warning(
            "latex_validation_error unbalanced_braces depth=%d", depth
        )
        return False
    return True


# Maps each requested section key to its prompt + the template arg name that
# carries the section's current LaTeX block.
_SECTION_PROMPTS = {
    "professional_summary": {
        "system": PROFESSIONAL_SUMMARY_SECTION_SYSTEM,
        "user": PROFESSIONAL_SUMMARY_SECTION_USER,
        "arg": "current_summary_latex",
    },
    "skills": {
        "system": SKILLS_SECTION_SYSTEM,
        "user": SKILLS_SECTION_USER,
        "arg": "skills_latex",
    },
    "work_experience": {
        "system": WORK_EXPERIENCE_SECTION_SYSTEM,
        "user": WORK_EXPERIENCE_SECTION_USER,
        "arg": "experience_latex",
    },
    "projects": {
        "system": PROJECT_SECTION_SYSTEM,
        "user": USER_PROJECT_SECTION_USER,
        "arg": "projects_latex",
    },
    "education": {
        "system": EDUCATION_SECTION_SYSTEM,
        "user": EDUCATION_SECTION_USER,
        "arg": "education_latex",
    },
}

# Keyword hints used to locate each section's section heading in the document.
_SECTION_KEYWORDS = {
    "professional_summary": [
        "summary",
        "profile",
        "about",
        "objective",
    ],
    "skills": ["skill", "technical", "competenc"],
    "work_experience": [
        "experience",
        "work",
        "employment",
        "professional",
    ],
    "projects": ["project"],
    "education": [
        "education",
        "academic",
        "university",
        "college",
        "degree",
    ],
}

_SECTION_HEADING_RE = re.compile(r"\\section\*?\s*\{(.*?)\}", re.DOTALL)


def _parse_latex_sections(latex: str) -> dict[str, str]:
    """Step 1: deterministically split the LaTeX document into named section blocks.

    Returns a dict with keys for each known section that is present, plus
    'header' (everything before the first section, e.g. preamble + name/contact)
    and 'footer' (everything after the last known section). 'professional_summary'
    falls back to the body content before the first section when no heading matches.
    """
    headings = list(_SECTION_HEADING_RE.finditer(latex))
    result: dict[str, str] = {}

    # Locate each known section's [start, end) span.
    spans: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(headings):
        title = m.group(1).lower()
        for key, kws in _SECTION_KEYWORDS.items():
            if any(kw in title for kw in kws):
                start = m.start()
                end = (
                    headings[i + 1].start()
                    if i + 1 < len(headings)
                    else len(latex)
                )
                spans[key] = (start, end)
                break

    # Header = everything before the first section heading (or whole doc if none).
    first_heading_start = (
        headings[0].start() if headings else len(latex)
    )
    result["header"] = latex[:first_heading_start]

    for key, (start, end) in spans.items():
        result[key] = latex[start:end]

    # professional_summary fallback: body before the first section.
    if "professional_summary" not in result:
        doc_start = latex.find("\\begin{document}")
        if doc_start != -1:
            body_start = doc_start + len("\\begin{document}")
            result["professional_summary"] = latex[
                body_start:first_heading_start
            ]

    # Footer = everything after the last known section span.
    last_end = max(
        (end for _, (_, end) in spans.items()),
        default=first_heading_start,
    )
    result["footer"] = latex[last_end:]
    return result


def _reconstruct_latex(
    original_latex: str,
    parsed: dict[str, str],
    optimized: dict[str, str],
) -> str:
    """Step 4: rebuild the full LaTeX document deterministically.

    Walks the original document heading-by-heading and emits, for each section,
    the ORIGINAL heading followed by the optimized body (if that section was
    optimized) or the original body. This guarantees every section heading
    appears exactly once and no section can be silently dropped, renamed, or
    have its header eaten by an adjacent optimized section. No LLM involved.
    """
    headings = list(_SECTION_HEADING_RE.finditer(original_latex))
    if not headings:
        # No headings -> nothing to splice; return original unchanged.
        logger.warning(
            "reconstruct_no_headings original_len=%d",
            len(original_latex),
        )
        return original_latex

    def _key_for(title: str):
        title = title.lower()
        for key, kws in _SECTION_KEYWORDS.items():
            if any(kw in title for kw in kws):
                return key
        return None

    logger.info(
        "reconstruct_headings found=%d titles=%s",
        len(headings),
        [h.group(1) for h in headings],
    )

    parts: list[str] = []
    # Everything before the first heading (preamble + name/contact).
    parts.append(original_latex[: headings[0].start()])

    for i, m in enumerate(headings):
        end = (
            headings[i + 1].start()
            if i + 1 < len(headings)
            else len(original_latex)
        )
        heading_line = original_latex[m.start() : m.end()]
        body = original_latex[m.end() : end]

        key = _key_for(m.group(1))
        new_block = optimized.get(key) if key else None
        if new_block:
            # Strip any heading the model may have emitted at the start of its
            # output so we never duplicate the (original) heading we emit here.
            emitted = _SECTION_HEADING_RE.match(new_block)
            if emitted:
                new_block = new_block[emitted.end() :].lstrip("\n")
            body = new_block
            logger.info(
                "reconstruct_section i=%d title=%r key=%s OPTIMIZED body_len=%d",
                i,
                m.group(1),
                key,
                len(body),
            )
        else:
            logger.info(
                "reconstruct_section i=%d title=%r key=%s ORIGINAL body_len=%d",
                i,
                m.group(1),
                key,
                len(body),
            )

        # Ensure the heading starts on its own line. The previous section's body
        # may end with a "%---...---" comment that was written on the SAME line as
        # this \section in the original (e.g. "%-----------PROJECTS-----------\section{Projects}").
        # Without a newline separator, that comment would swallow this heading and
        # make the section title disappear from the compiled PDF.
        parts.append("\n" + heading_line)
        parts.append(body)

    rebuilt = "".join(parts)
    # Final sanity check: confirm every original heading survived in the output.
    for h in headings:
        if h.group(0) not in rebuilt:
            logger.error(
                "reconstruct_MISSING_HEADING title=%r -- this heading was dropped!",
                h.group(1),
            )
    logger.info(
        "reconstruct_done headings_in_output=%d projects_present=%s",
        sum(1 for h in headings if h.group(0) in rebuilt),
        r"\section{Projects}" in rebuilt,
    )
    return rebuilt


async def _compile_latex_to_pdf_via_api(
    latex: str, db: Optional[AsyncSession] = None, max_retries: int = 2
) -> Optional[bytes]:
    """Compile LaTeX to PDF via the self-hosted AWS Lambda (TeXLive) compiler.

    POSTs the .tex as JSON `{"latex_base64": "<base64 source>"}` (matches the Lambda
    handler's `latex_base64` branch — safest for backslash-heavy source). On success
    Over a Lambda Function URL the handler's `isBase64Encoded` envelope is stripped
    by API Gateway, so a successful compile returns the RAW PDF bytes with
    `Content-Type: application/pdf` — we capture `resp.content` directly. If the
    Lambda had to install missing packages on the fly it echoes them in the
    `X-Latex-Fallback-Used` header; those are upserted into latex_package_usage when
    `db` is provided. On failure the Lambda returns 422 with a JSON body containing
    the LaTeX log tail. Retries up to `max_retries` times with a short backoff.
    Returns raw PDF bytes, or None if every attempt fails.
    """
    url = settings.LATEX_COMPILE_URL
    if not url:
        logger.error(
            "latex_compile_no_url LATEX_COMPILE_URL is not configured"
        )
        return None

    payload = {
        "latex_base64": base64.b64encode(latex.encode("utf-8")).decode(
            "utf-8"
        )
    }
    logger.info(
        "latex_compile_start url_set=%s latex_len=%d retries=%d",
        bool(url),
        len(latex),
        max_retries,
    )

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=90, follow_redirects=True
            ) as client:
                resp = await client.post(url, json=payload)
            logger.info(
                "latex_compile_attempt attempt=%d/%d status=%d ctype=%s",
                attempt,
                max_retries,
                resp.status_code,
                resp.headers.get("content-type"),
            )

            if resp.status_code == 200:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/pdf" in ctype:
                    pdf = resp.content
                    if pdf.startswith(b"%PDF"):
                        logger.info(
                            "latex_compile_ok attempt=%d/%d bytes=%d",
                            attempt,
                            max_retries,
                            len(pdf),
                        )
                        await _record_fallback_packages(resp, db)
                        return pdf
                    logger.warning(
                        "latex_compile_bad_pdf attempt=%d head=%s",
                        attempt,
                        pdf[:40],
                    )
                    return None
                logger.warning(
                    "latex_compile_bad_response attempt=%d ctype=%s head=%s",
                    attempt,
                    ctype,
                    resp.content[:40],
                )
                return None

            # 422 etc: surface the log tail so callers can debug missing packages.
            try:
                detail = resp.json()
                logger.warning(
                    "latex_compile_failed attempt=%d status=%d detail=%s",
                    attempt,
                    resp.status_code,
                    detail,
                )
            except Exception:
                logger.warning(
                    "latex_compile_failed attempt=%d status=%d body=%s",
                    attempt,
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as e:
            logger.warning(
                "latex_compile_error attempt=%d error=%s",
                attempt,
                str(e),
            )

        if attempt < max_retries:
            await asyncio.sleep(1 * attempt)

    logger.error("latex_compile_all_failed")
    return None


async def _record_fallback_packages(resp, db) -> None:
    """If the Lambda installed packages on the fly (X-Latex-Fallback-Used header),
    upsert each into latex_package_usage, incrementing its download count."""
    if db is None:
        return
    header = resp.headers.get("X-Latex-Fallback-Used")
    if not header:
        return
    try:
        packages = json.loads(header)
    except Exception:
        logger.warning(
            "latex_fallback_header_parse_failed header=%s", header[:200]
        )
        return
    if not isinstance(packages, list):
        return
    repo = LatexPackageRepository(db)
    for pkg in packages:
        if not pkg:
            continue
        try:
            await repo.record_usage(pkg)
        except Exception as e:
            logger.warning(
                "latex_fallback_record_failed pkg=%s error=%s",
                pkg,
                str(e),
            )
