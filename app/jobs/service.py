import uuid
from app.common.logging import get_logger
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job, JobStatus, is_valid_job_transition
from app.jobs.repository import JobRepository
from app.jobs.schemas import CreateJobRequest, JobResponse, JobDetailResponse, JobListResponse
from app.job_jd.service import JobJDService
from app.users.models import User
from app.common.exceptions import NotFoundError, InvalidTransitionError, ForbiddenError

logger = get_logger(__name__)


class JobService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = JobRepository(db)

    def _assert_ownership(self, job: Job, user_id: uuid.UUID) -> None:
        if job.user_id != user_id:
            raise ForbiddenError("You do not have access to this job")
    async def create(self, req: CreateJobRequest, user: User) -> JobDetailResponse:
        try:
            job = Job(user_id=user.id, workday_url=req.workday_url, status=JobStatus.NEW)
            self.db.add(job)
            await self.db.flush()

            jd_svc = JobJDService(self.db)
            jd, _ = await jd_svc.parse_and_store(job.id, req.workday_url, user, ai=req.ai)

            job.status = JobStatus.JD_PARSED
            await self.db.flush()

            logger.info("job_created job_id=%s user_id=%s", str(job.id), str(user.id))
            await self.db.refresh(job)

            return JobDetailResponse.from_job(job, jd)
        except Exception as e:
            # log and re-raise so the outer dependency session can rollback
            logger.info("[Warning] jd_parse_failed_on_create job_id=%s error=%s", str(user.id), str(e))
            raise

    async def list(self, user: User, status: Optional[JobStatus] = None) -> JobListResponse:
        rows = await self.repo.list_with_jd(user.id, status=status)
        total = await self.repo.count_by_user(user.id, status=status)
        return JobListResponse(
            items=[JobDetailResponse.from_job(job, jd) for job, jd in rows],
            total=total,
        )

    async def get(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self._assert_ownership(job, user.id)
        return job

    async def delete(self, job_id: uuid.UUID, user: User) -> None:
        job = await self.get(job_id, user)
        await self.repo.delete(job)
        logger.info("job_deleted job_id=%s", str(job_id))

    async def update_status(self, job_id: uuid.UUID, new_status: JobStatus, user: User) -> Job:
        job = await self.get(job_id, user)
        if not is_valid_job_transition(job.status, new_status):
            raise InvalidTransitionError(job.status.value, new_status.value)
        await self.repo.update(job, status=new_status)
        logger.info("job_status_updated job_id=%s status=%s", str(job_id), new_status.value)
        return job

    async def reparse_jd(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.get(job_id, user)
        jd_svc = JobJDService(self.db)
        await jd_svc.parse_and_store(job.id, job.workday_url, user)
        await self.repo.update(job, status=JobStatus.JD_PARSED)
        return job
