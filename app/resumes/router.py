import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.resumes.service import ResumeService
from app.resumes.schemas import (
    CreateResumeUploadUrlsResponse,
    GenerateAiResumeRequest,
    GenerateAiResumeResponse,
    GetResumeDownloadResponse,
    CompileResumeRequest,
)

router = APIRouter()


@router.post(
    "/upload-url",
    response_model=CreateResumeUploadUrlsResponse,
    status_code=201,
)
async def create_resume_upload_url(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned PUT URL so the client uploads the ORIGINAL resume LaTeX to R2.

    Only the original resume is uploaded by the user; the AI resume is generated server-side.
    """
    return await ResumeService(db).create_upload_url(current_user)


@router.post(
    "/generate/{job_id}",
    response_model=GenerateAiResumeResponse,
    status_code=201,
)
async def generate_ai_resume(
    job_id: uuid.UUID,
    payload: GenerateAiResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Optimize the requested resume sections for a job and return a download URL.

    The client sends `sections` (e.g. ["summary", "skills", "experience"]) which are
    each rewritten by a dedicated LLM pass, in order, then compiled to PDF.
    """
    return await ResumeService(db).generate_ai(
        job_id, payload.sections, current_user
    )


@router.post(
    "/compile/{job_id}",
    response_model=GetResumeDownloadResponse,
    status_code=200,
)
async def compile_resume_for_job(
    job_id: uuid.UUID,
    payload: CompileResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compile custom LaTeX provided by frontend to PDF, save both .tex and .pdf to R2, and return presigned GET URL."""
    return await ResumeService(db).compile_custom_latex(
        job_id, payload.latex, current_user
    )


@router.post(
    "/finalize/{resume_type}",
    response_model=GetResumeDownloadResponse,
    status_code=201,
)
async def finalize_resume(
    resume_type: Literal["original"],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compile the just-uploaded ORIGINAL LaTeX to PDF and return the PDF download URL.

    Call this after the client PUTs the LaTeX to the presigned URL from /upload-url.
    (The AI resume is compiled automatically during /generate, so this is original-only.)
    """
    return await ResumeService(db).finalize_resume(
        resume_type, current_user
    )


@router.get(
    "/download/{version}", response_model=GetResumeDownloadResponse
)
async def get_resume_download_url(
    version: Literal["original", "ai"],
    job_id: str,
    is_pdf: bool = Query(True, alias="isPdf"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned GET URL to download a stored resume copy (PDF or LaTeX source)."""
    return await ResumeService(db).get_download_url(
        current_user, version, job_id=job_id, is_pdf=is_pdf
    )
