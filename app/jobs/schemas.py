import uuid
from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, field_validator

from app.jobs.models import JobStatus
from app.common.validators import is_http_url


class CreateJobRequest(BaseModel):
    workday_url: str
    ai: bool = True
    applied: bool = False

    @field_validator("workday_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not is_http_url(v):
            raise ValueError(
                "workday_url must be a valid HTTP/HTTPS URL"
            )
        return v


class UpdateJobStatusRequest(BaseModel):
    status: JobStatus


class JobResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    workday_url: str
    status: JobStatus
    referral_received: bool = False
    optimized_resume_pdf_url: Optional[str] = None
    optimized_resume_latex_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    """Extended view returned by create/list, including JD-derived fields."""

    company: Optional[str] = None
    role: Optional[str] = None
    workday_job_id: Optional[str] = None

    @classmethod
    def from_job(
        cls, job: Any, jd: Optional[Any] = None
    ) -> "JobDetailResponse":
        detail = cls.model_validate(job)
        if jd is not None:
            detail.company = jd.company
            detail.role = jd.role
            detail.workday_job_id = jd.workday_job_id
        return detail


class JobListResponse(BaseModel):
    items: list[JobDetailResponse]
    total: int
