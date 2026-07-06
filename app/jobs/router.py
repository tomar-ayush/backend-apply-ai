import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.jobs.models import JobStatus
from app.jobs.schemas import CreateJobRequest, JobResponse, JobListResponse, UpdateJobStatusRequest
from app.jobs.service import JobService
from app.job_jd.schemas import JobJDResponse
from app.job_jd.service import JobJDService
from app.referrals.schemas import GenerateReferralsResponse, ReferralResponse
from app.referrals.service import ReferralService
from app.resumes.schemas import GenerateResumeResponse, SelectResumeRequest, ResumeResponse
from app.resumes.service import ResumeService

router = APIRouter()


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    req: CreateJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).create(req, current_user)
    return JobResponse.model_validate(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[JobStatus] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await JobService(db).list(current_user, status=status)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return JobResponse.model_validate(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await JobService(db).delete(job_id, current_user)


@router.patch("/{job_id}/status", response_model=JobResponse)
async def update_job_status(
    job_id: uuid.UUID,
    req: UpdateJobStatusRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).update_status(job_id, req.status, current_user)
    return JobResponse.model_validate(job)


# --- JD routes ---

@router.get("/{job_id}/jd", response_model=JobJDResponse)
async def get_jd(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await JobService(db).get(job_id, current_user)
    jd = await JobJDService(db).get_by_job_id(job_id)
    return JobJDResponse.model_validate(jd)


@router.post("/{job_id}/parse", response_model=JobJDResponse)
async def reparse_jd(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    jd_svc = JobJDService(db)
    jd, _ = await jd_svc.parse_and_store(job.id, job.workday_url, current_user)
    return JobJDResponse.model_validate(jd)


# --- Referral routes ---

@router.post("/{job_id}/referrals/generate", response_model=GenerateReferralsResponse)
async def generate_referrals(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return await ReferralService(db).generate(job, current_user)


@router.get("/{job_id}/referrals", response_model=list[ReferralResponse])
async def list_referrals(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await JobService(db).get(job_id, current_user)
    referrals = await ReferralService(db).list_by_job(job_id)
    return [ReferralResponse.model_validate(r) for r in referrals]


# --- Resume routes ---

@router.post("/{job_id}/resume/generate", response_model=GenerateResumeResponse)
async def generate_resume(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return await ResumeService(db).generate(job, current_user)


@router.get("/{job_id}/resume", response_model=ResumeResponse)
async def get_resume(
    job_id: uuid.UUID,
    version: str = Query("optimized"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return await ResumeService(db).get(job, version)


@router.post("/{job_id}/resume/select", response_model=ResumeResponse)
async def select_resume(
    job_id: uuid.UUID,
    req: SelectResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return await ResumeService(db).get(job, req.version)
