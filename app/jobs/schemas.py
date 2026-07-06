import uuid
from datetime import datetime
from typing import Optional, Any, Dict

from pydantic import BaseModel, HttpUrl, field_validator

from app.jobs.models import JobStatus


class CreateJobRequest(BaseModel):
    workday_url: str

    @field_validator("workday_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("workday_url must be a valid HTTP/HTTPS URL")
        return v


class UpdateJobStatusRequest(BaseModel):
    status: JobStatus


class JobResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    company: Optional[str] = None
    role: Optional[str] = None
    workday_job_id: Optional[str] = None
    workday_url: str
    status: JobStatus
    optimized_resume_pdf_url: Optional[str] = None
    optimized_resume_latex_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
