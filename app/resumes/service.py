import uuid
import httpx
from typing import Optional

from app.common.logging import get_logger

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.repository import JobRepository
from app.job_jd.repository import JobJDRepository
from app.users.models import User
from app.users.repository import UserRepository
from app.users.service import UserService
from app.llm.client import LLMClient
from app.llm.prompts import RESUME_SUMMARY_OPTIMIZE_SYSTEM, RESUME_SUMMARY_OPTIMIZE_USER
from app.storage.r2 import r2_storage
from app.resumes.schemas import CreateResumeUploadUrlsResponse, GenerateAiResumeResponse, GetResumeDownloadResponse
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

    def _resume_key(self, user_id: uuid.UUID, kind: str) -> str:
        """kind is 'original' or 'ai'. LaTeX source stored here."""
        return f"resume/{user_id}/{kind}_resume.tex"

    def _resume_pdf_key(self, user_id: uuid.UUID, kind: str) -> str:
        """Matching PDF, same naming convention with a _pdf postfix."""
        return f"resume/{user_id}/{kind}_resume_pdf.pdf"

    async def create_upload_url(self, user: User) -> CreateResumeUploadUrlsResponse:
        """Mint a presigned PUT URL so the client uploads the LaTeX source of one resume copy.

        The client uploads LaTeX only; the server compiles it to PDF (see finalize_resume)
        and stores both. The canonical LaTeX URL is recorded immediately since the key
        is deterministic.
        """
        key = self._resume_key(user.id, "original")
        presigned_url = r2_storage.generate_presigned_put_url(key, RESUME_TEX_CONTENT_TYPE, PRESIGN_EXPIRY_SECONDS)
        final = r2_storage._public_url(key)

        user_repo = UserRepository(self.db)
        await user_repo.update(user, original_resume_latex_url=final)

        logger.info("resume_upload_url_created user_id=%s type=%s", str(user.id), "original")
        return CreateResumeUploadUrlsResponse(latex_presigned_url=presigned_url)

    async def finalize_resume(self, resume_type: str, user: User) -> GetResumeDownloadResponse:
        """Compile the just-uploaded LaTeX to PDF via latexonline.cc and store it.

        Call this after the client PUTs the LaTeX to the presigned URL. Returns the PDF
        download URL. If compilation fails, the PDF URL is None but the LaTeX is kept.
        """
        kind = resume_type
        latex_key = self._resume_key(user.id, kind)
        latex_text = r2_storage.download_text(latex_key)

        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(latex_text)
        if pdf_bytes:
            pdf_key = self._resume_pdf_key(user.id, kind)
            pdf_url = r2_storage.upload_bytes(pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE)
        else:
            logger.warning("resume_pdf_compile_failed type=%s user_id=%s", kind, str(user.id))

        user_repo = UserRepository(self.db)
        await user_repo.update(user, original_resume_pdf_url=pdf_url)

        download_url = (
            r2_storage.generate_presigned_get_url(r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS)
            if pdf_url else None
        )
        logger.info("resume_finalized type=%s has_pdf=%s", kind, bool(pdf_url))
        return GetResumeDownloadResponse(
            version=kind,
            download_url=download_url,
            message=(
                f"{kind} resume compiled to PDF" if pdf_url
                else f"{kind} LaTeX uploaded but PDF compilation failed"
            ),
        )

    async def generate_ai(self, job_id: uuid.UUID, user: User) -> GenerateAiResumeResponse:
        """Generate an ATS-friendly AI resume for a job, validate, upload, return a download URL.

        Only the professional summary section is rewritten. The result is stored as
        LaTeX only (no PDF — the client compiles). Validated with pylatexenc before upload.
        """
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError("LLM provider and API key must be configured")

        if not user.original_resume_latex_url:
            raise BadRequestError("Original LaTeX resume must be uploaded before generating the AI resume")

        jd = await self.jd_repo.get_by_job_id(job_id)
        if jd is None:
            raise BadRequestError("Job description must be parsed before generating resume")

        latex_key = r2_storage.key_from_url(user.original_resume_latex_url)
        original_latex = r2_storage.download_text(latex_key)

        skills = jd.skills or {}
        required = skills.get("required", [])
        keywords = jd.keywords or []

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        prompt = RESUME_SUMMARY_OPTIMIZE_USER.format(
            jd_summary=jd.llm_summary or "",
            required_skills=", ".join(required),
            keywords=", ".join(keywords[:30]),
            latex_content=original_latex,
        )

        logger.info("ai_resume_generate_start job_id=%s user_id=%s", str(job_id), str(user.id))
        optimized_latex = await llm.complete(system=RESUME_SUMMARY_OPTIMIZE_SYSTEM, user=prompt, model=user.current_llm_model)

        validated = _validate_latex(optimized_latex)
        if not validated:
            logger.warning("ai_resume_latex_validation_failed job_id=%s", str(job_id))

        ai_key = self._resume_key(user.id, "ai")
        latex_url = r2_storage.upload_text(ai_key, optimized_latex, RESUME_TEX_CONTENT_TYPE)

        # Compile the optimized LaTeX to PDF via latexonline.cc and store it too.
        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(optimized_latex)
        if pdf_bytes:
            pdf_key = self._resume_pdf_key(user.id, "ai")
            pdf_url = r2_storage.upload_bytes(pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE)
        else:
            logger.warning("ai_resume_pdf_compile_failed job_id=%s", str(job_id))

        user_repo = UserRepository(self.db)
        await user_repo.update(user, ai_resume_latex_url=latex_url, ai_resume_pdf_url=pdf_url)

        download_url = (
            r2_storage.generate_presigned_get_url(r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS)
            if pdf_url else None
        )
        logger.info("ai_resume_generated job_id=%s validated=%s has_pdf=%s", str(job_id), validated, bool(pdf_url))
        return GenerateAiResumeResponse(
            download_url=download_url,
            validated=validated,
        )

    async def get_download_url(self, user: User, version: str) -> GetResumeDownloadResponse:
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

        download_url = r2_storage.generate_presigned_get_url(r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS)
        return GetResumeDownloadResponse(
            version=version,
            download_url=download_url,
            message=f"Use the download_url to fetch the {version} resume PDF",
        )


def _validate_latex(content: str) -> bool:
    """Validate LaTeX is parseable using pylatexenc. Returns True if it parses cleanly."""
    try:
        from pylatexenc.latexwalker import LatexWalker
        walker = LatexWalker(content)
        walker.get_latex_breakup()  # parse the whole document
        return True
    except Exception as e:
        logger.warning("latex_validation_error error=%s", str(e))
        return False


async def _compile_latex_to_pdf_via_api(latex: str) -> Optional[bytes]:
    """Compile LaTeX to PDF using the latexonline.cc API (xelatex engine).

    Uses POST with form fields (not a URL-encoded GET) so long resumes don't blow
    past URL-length limits. Returns raw PDF bytes, or None if compilation fails.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://latexonline.cc/compile",
                data={"text": latex, "command": "xelatex"},
            )
        if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
            return resp.content
        logger.warning("latexonline_compile_failed status=%s", resp.status_code)
    except Exception as e:
        logger.warning("latexonline_error error=%s", str(e))
    return None
