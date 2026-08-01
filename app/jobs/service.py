import uuid
from app.common.logging import get_logger
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.common.events import event_bus
from app.common.exceptions import (
    NotFoundError,
    InvalidTransitionError,
)
from app.jobs.models import Job, JobStatus, is_valid_job_transition
from app.jobs.repository import JobRepository
from app.jobs.schemas import (
    CreateJobRequest,
    JobDetailResponse,
    JobListResponse,
)
from app.job_jd.service import JobJDService
from app.users.models import User

logger = get_logger(__name__)


class JobService(BaseService):
    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.repo = JobRepository(db)

    async def create(
        self, req: CreateJobRequest, user: User
    ) -> JobDetailResponse:
        job = Job(
            user_id=user.id,
            workday_url=req.workday_url,
            status=JobStatus.NEW,
        )
        self.db.add(job)
        await self.db.flush()

        jd_svc = JobJDService(self.db)
        try:
            jd, _ = await jd_svc.parse_and_store(
                job.id, req.workday_url, user, ai=req.ai
            )
            job.status = JobStatus.JD_PARSED
            await self.db.flush()
            logger.info(
                "job_created job_id=%s user_id=%s",
                str(job.id),
                str(user.id),
            )
            await self.db.refresh(job)
            return JobDetailResponse.from_job(job, jd)
        except Exception:
            logger.warning(
                "job_create_jd_parse_failed job_id=%s user_id=%s",
                str(job.id),
                str(user.id),
            )
            raise

    async def list(
        self, user: User, status: Optional[JobStatus] = None
    ) -> JobListResponse:
        rows = await self.repo.list_with_jd(user.id, status=status)
        total = await self.repo.count_by_user(user.id, status=status)
        return JobListResponse(
            items=[
                JobDetailResponse.from_job(job, jd) for job, jd in rows
            ],
            total=total,
        )

    async def get(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self.assert_ownership(job, user.id, "job")
        return job

    async def delete(self, job_id: uuid.UUID, user: User) -> None:
        job = await self.get(job_id, user)
        await self.repo.delete(job)
        logger.info("job_deleted job_id=%s", str(job_id))

    async def update_status(
        self, job_id: uuid.UUID, new_status: JobStatus, user: User
    ) -> Job:
        job = await self.get(job_id, user)
        if not is_valid_job_transition(job.status, new_status):
            raise InvalidTransitionError(
                job.status.value, new_status.value
            )

        kwargs = {"status": new_status}
        if new_status == JobStatus.REFERRAL_RECEIVED:
            kwargs["referral_received"] = True

        await self.repo.update(job, **kwargs)
        logger.info(
            "job_status_updated job_id=%s status=%s",
            str(job_id),
            new_status.value,
        )

        # Push real-time update so the frontend can invalidate TanStack Query
        # instead of polling.
        await event_bus.publish(
            f"user:{user.id}",
            {
                "type": "job_status_updated",
                "job_id": str(job_id),
                "status": new_status.value,
            },
        )
        return job

    async def reparse_jd(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.get(job_id, user)
        jd_svc = JobJDService(self.db)
        await jd_svc.parse_and_store(job.id, job.workday_url, user)
        await self.repo.update(job, status=JobStatus.JD_PARSED)
        return job
