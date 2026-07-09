import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.resumes.service import ResumeService
from app.resumes.schemas import (
    CreateResumeUploadUrlsResponse,
    GenerateAiResumeResponse,
    GetResumeDownloadResponse,
)

router = APIRouter()


@router.post("/upload-url/{resume_type}", response_model=CreateResumeUploadUrlsResponse, status_code=201)
async def create_resume_upload_url(
    resume_type: Literal["original", "ai"],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned PUT URL so the client uploads the requested resume copy to R2."""
    return await ResumeService(db).create_upload_url(resume_type, current_user)


@router.post("/generate/{job_id}", response_model=GenerateAiResumeResponse, status_code=201)
async def generate_ai_resume(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an ATS-friendly AI resume for a job, validate, upload, return a download URL."""
    return await ResumeService(db).generate_ai(job_id, current_user)


@router.get("/download/{version}", response_model=GetResumeDownloadResponse)
async def get_resume_download_url(
    version: Literal["original", "ai"],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned GET URL to download a stored resume copy."""
    return await ResumeService(db).get_download_url(current_user, version)
