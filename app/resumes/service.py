import uuid
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
        """kind is 'original' or 'ai'. Stores LaTeX only; client converts to PDF."""
        return f"resume/{user_id}/{kind}_resume.tex"

    async def create_upload_url(self, resume_type: str, user: User) -> CreateResumeUploadUrlsResponse:
        """Mint a presigned PUT URL so the client uploads one resume copy to R2 directly.

        The client specifies which copy ('original' or 'ai'); the canonical final URL
        is recorded on the user immediately since the key is deterministic.
        """
        key = self._resume_key(user.id, resume_type)
        presigned_url = r2_storage.generate_presigned_put_url(key, RESUME_TEX_CONTENT_TYPE, PRESIGN_EXPIRY_SECONDS)
        final_url = r2_storage._public_url(key)

        user_repo = UserRepository(self.db)
        if resume_type == "ai":
            await user_repo.update(user, ai_resume_latex_url=final_url)
        else:
            await user_repo.update(user, original_resume_latex_url=final_url)

        logger.info("resume_upload_url_created user_id=%s type=%s", str(user.id), resume_type)
        return CreateResumeUploadUrlsResponse(presigned_url=presigned_url)

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
        optimized_latex = await llm.complete(system=RESUME_SUMMARY_OPTIMIZE_SYSTEM, user=prompt)

        validated = _validate_latex(optimized_latex)
        if not validated:
            logger.warning("ai_resume_latex_validation_failed job_id=%s", str(job_id))

        ai_key = self._resume_key(user.id, "ai")
        latex_url = r2_storage.upload_text(ai_key, optimized_latex, RESUME_TEX_CONTENT_TYPE)
        download_url = r2_storage.generate_presigned_get_url(ai_key, PRESIGN_EXPIRY_SECONDS)

        user_repo = UserRepository(self.db)
        await user_repo.update(user, ai_resume_latex_url=latex_url)

        logger.info("ai_resume_generated job_id=%s validated=%s", str(job_id), validated)
        return GenerateAiResumeResponse(
            download_url=download_url,
            validated=validated,
        )

    async def get_download_url(self, user: User, version: str) -> GetResumeDownloadResponse:
        """Return a presigned GET URL for a stored resume copy (original or ai)."""
        if version == "ai":
            latex_url = user.ai_resume_latex_url
        else:
            latex_url = user.original_resume_latex_url

        if not latex_url:
            return GetResumeDownloadResponse(
                version=version,
                latex_url=None,
                download_url=None,
                message=f"No {version} resume uploaded yet",
            )

        key = r2_storage.key_from_url(latex_url)
        download_url = r2_storage.generate_presigned_get_url(key, PRESIGN_EXPIRY_SECONDS)
        return GetResumeDownloadResponse(
            version=version,
            latex_url=latex_url,
            download_url=download_url,
            message=f"Use the download_url to fetch the {version} resume .tex",
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
