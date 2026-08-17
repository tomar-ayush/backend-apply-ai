import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.jobs.models import JobStatus
from app.jobs.schemas import (
    CreateJobRequest,
    JobResponse,
    JobDetailResponse,
    JobListResponse,
    UpdateJobStatusRequest,
)
from app.jobs.service import JobService
from app.job_jd.schemas import JobJDResponse, UpdateJDRequest
from app.job_jd.service import JobJDService
from app.referrals.schemas import (
    GenerateReferralsResponse,
    ReferralResponse,
    BulkCreateReferralsRequest,
)
from app.referrals.service import ReferralService

router = APIRouter()


@router.post("", response_model=JobDetailResponse, status_code=201)
async def create_job(
    req: CreateJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await JobService(db).create(req, current_user)


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
    return await JobService(db).get(job_id, current_user)


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
    return await JobService(db).update_status(
        job_id, req.status, current_user
    )


# --- JD routes ---


def _add_missing_keywords(jd, user: User) -> JobJDResponse:
    resp = JobJDResponse.model_validate(jd)
    
    jd_kws = []
    if isinstance(jd.keywords, list):
        jd_kws.extend(jd.keywords)
    if isinstance(jd.skills, dict):
        if isinstance(jd.skills.get("required"), list):
            jd_kws.extend(jd.skills["required"])
        if isinstance(jd.skills.get("preferred"), list):
            jd_kws.extend(jd.skills["preferred"])
            
    seen = set()
    unique_jd_kws = []
    for k in jd_kws:
        if k.lower() not in seen:
            seen.add(k.lower())
            unique_jd_kws.append(k)
            
    user_kws = set(k.lower() for k in user.resume_keywords) if user.resume_keywords and isinstance(user.resume_keywords, list) else set()
    resp.missing_keywords = [k for k in unique_jd_kws if k.lower() not in user_kws]
    
    return resp


@router.get("/{job_id}/jd", response_model=JobJDResponse)
async def get_jd(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await JobService(db).get(job_id, current_user)
    jd = await JobJDService(db).get_by_job_id(job_id)
    return _add_missing_keywords(jd, current_user)


@router.post("/{job_id}/parse", response_model=JobJDResponse)
async def reparse_jd(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    jd_svc = JobJDService(db)
    jd, _ = await jd_svc.parse_and_store(
        job.id, job.workday_url, current_user
    )
    return _add_missing_keywords(jd, current_user)


@router.patch("/{job_id}/jd", response_model=JobJDResponse)
async def update_jd(
    job_id: uuid.UUID,
    req: UpdateJDRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partially update the JD."""
    await JobService(db).get(job_id, current_user)
    jd = await JobJDService(db).update(job_id, req)
    return _add_missing_keywords(jd, current_user)


# --- Referral routes ---


@router.post(
    "/{job_id}/referrals/generate",
    response_model=GenerateReferralsResponse,
)
async def generate_referrals(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await JobService(db).get(job_id, current_user)
    return await ReferralService(db).generate(job, current_user)


@router.post(
    "/{job_id}/referrals",
    response_model=list[ReferralResponse],
    status_code=201,
)
async def create_referrals(
    job_id: uuid.UUID,
    req: BulkCreateReferralsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create referrals from a user-provided list (name, linkedin_url, priority)."""
    job = await JobService(db).get(job_id, current_user)
    created = await ReferralService(db).create_many_referrals(
        job, req.referrals
    )
    return [ReferralResponse.model_validate(r) for r in created]


@router.get(
    "/{job_id}/referrals", response_model=list[ReferralResponse]
)
async def list_referrals(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await JobService(db).get(job_id, current_user)
    referrals = await ReferralService(db).list_by_job(job_id)
    return referrals
