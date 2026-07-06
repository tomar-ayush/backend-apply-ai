import uuid
import structlog
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job, JobStatus, is_valid_job_transition
from app.jobs.repository import JobRepository
from app.jobs.schemas import CreateJobRequest, JobResponse, JobListResponse
from app.job_jd.service import JobJDService
from app.users.models import User
from app.common.exceptions import NotFoundError, InvalidTransitionError, ForbiddenError

logger = structlog.get_logger()


class JobService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = JobRepository(db)

    async def _assert_ownership(self, job: Job, user_id: uuid.UUID) -> None:
        if job.user_id != user_id:
            raise ForbiddenError("You do not have access to this job")

    async def create(self, req: CreateJobRequest, user: User) -> Job:
        job = await self.repo.create(
            user_id=user.id,
            workday_url=req.workday_url,
            status=JobStatus.NEW,
        )
        logger.info("job_created", job_id=str(job.id), user_id=str(user.id))

        jd_svc = JobJDService(self.db)
        try:
            jd, parsed = await jd_svc.parse_and_store(job.id, req.workday_url, user)
            updates = {"status": JobStatus.JD_PARSED}
            if parsed.get("company"):
                updates["company"] = parsed["company"]
            if parsed.get("role"):
                updates["role"] = parsed["role"]
            if parsed.get("workday_job_id"):
                updates["workday_job_id"] = parsed["workday_job_id"]
            await self.repo.update(job, **updates)
        except Exception as e:
            logger.warning("jd_parse_failed_on_create", job_id=str(job.id), error=str(e))

        return job

    async def list(self, user: User, status: Optional[JobStatus] = None) -> JobListResponse:
        items = await self.repo.list_by_user(user.id, status=status)
        total = await self.repo.count_by_user(user.id, status=status)
        return JobListResponse(items=[JobResponse.model_validate(j) for j in items], total=total)

    async def get(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        await self._assert_ownership(job, user.id)
        return job

    async def delete(self, job_id: uuid.UUID, user: User) -> None:
        job = await self.get(job_id, user)
        await self.repo.delete(job)
        logger.info("job_deleted", job_id=str(job_id))

    async def update_status(self, job_id: uuid.UUID, new_status: JobStatus, user: User) -> Job:
        job = await self.get(job_id, user)
        if not is_valid_job_transition(job.status, new_status):
            raise InvalidTransitionError(job.status.value, new_status.value)
        await self.repo.update(job, status=new_status)
        logger.info("job_status_updated", job_id=str(job_id), status=new_status.value)
        return job

    async def reparse_jd(self, job_id: uuid.UUID, user: User) -> Job:
        job = await self.get(job_id, user)
        jd_svc = JobJDService(self.db)
        await jd_svc.parse_and_store(job.id, job.workday_url, user)
        await self.repo.update(job, status=JobStatus.JD_PARSED)
        return job
