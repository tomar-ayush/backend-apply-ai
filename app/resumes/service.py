import uuid
import os
import subprocess
import tempfile
import structlog
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job, JobStatus
from app.jobs.repository import JobRepository
from app.job_jd.repository import JobJDRepository
from app.users.models import User
from app.users.service import UserService
from app.llm.client import LLMClient
from app.llm.prompts import RESUME_OPTIMIZE_SYSTEM, RESUME_OPTIMIZE_USER
from app.storage.r2 import r2_storage
from app.resumes.schemas import GenerateResumeResponse, ResumeResponse
from app.common.exceptions import BadRequestError, NotFoundError

logger = structlog.get_logger()


def _compile_latex_to_pdf(latex_content: str) -> Optional[bytes]:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "resume.tex")
            pdf_path = os.path.join(tmpdir, "resume.pdf")

            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(latex_content)

            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                capture_output=True,
                timeout=60,
            )
            # Run twice for cross-references
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                capture_output=True,
                timeout=60,
            )

            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return f.read()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("latex_compile_failed", error=str(e))
    return None


class ResumeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.job_repo = JobRepository(db)
        self.jd_repo = JobJDRepository(db)

    async def generate(self, job: Job, user: User) -> GenerateResumeResponse:
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError("LLM provider and API key must be configured")

        if not user.original_resume_latex_url:
            raise BadRequestError("Original LaTeX resume must be uploaded to your profile before optimization")

        jd = await self.jd_repo.get_by_job_id(job.id)
        if jd is None:
            raise BadRequestError("Job description must be parsed before generating resume")

        latex_key = r2_storage.key_from_url(user.original_resume_latex_url)
        original_latex = r2_storage.download_text(latex_key)

        skills = jd.skills or {}
        required = skills.get("required", [])
        keywords = jd.keywords or []

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        prompt = RESUME_OPTIMIZE_USER.format(
            jd_summary=jd.llm_summary or "",
            required_skills=", ".join(required),
            keywords=", ".join(keywords[:30]),
            latex_content=original_latex,
        )

        logger.info("resume_optimize_start", job_id=str(job.id))
        optimized_latex = await llm.complete(system=RESUME_OPTIMIZE_SYSTEM, user=prompt)

        latex_key = f"jobs/{job.id}/optimized_resume.tex"
        latex_url = r2_storage.upload_text(latex_key, optimized_latex, "text/x-tex")

        pdf_url: Optional[str] = None
        pdf_bytes = _compile_latex_to_pdf(optimized_latex)
        if pdf_bytes:
            pdf_key = f"jobs/{job.id}/optimized_resume.pdf"
            pdf_url = r2_storage.upload_bytes(pdf_key, pdf_bytes, "application/pdf")
        else:
            logger.warning("pdf_compilation_skipped", job_id=str(job.id))

        await self.job_repo.update(
            job,
            optimized_resume_latex_url=latex_url,
            optimized_resume_pdf_url=pdf_url,
            status=JobStatus.RESUME_GENERATED,
        )

        logger.info("resume_generated", job_id=str(job.id), has_pdf=bool(pdf_url))
        return GenerateResumeResponse(
            latex_url=latex_url,
            pdf_url=pdf_url,
            message="Resume optimized successfully" + ("" if pdf_url else " (PDF compilation unavailable)"),
        )

    async def get(self, job: Job, version: str = "optimized") -> ResumeResponse:
        if version == "optimized":
            return ResumeResponse(
                version="optimized",
                pdf_url=job.optimized_resume_pdf_url,
                latex_url=job.optimized_resume_latex_url,
            )
        from app.users.repository import UserRepository
        user_repo = UserRepository(self.db)
        user = await user_repo.get_by_id(job.user_id)
        return ResumeResponse(
            version="original",
            pdf_url=user.original_resume_pdf_url if user else None,
            latex_url=user.original_resume_latex_url if user else None,
        )
