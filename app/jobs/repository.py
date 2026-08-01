import uuid
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.repository import BaseRepository
from app.jobs.models import Job, JobStatus
from app.job_jd.models import JobJD


class JobRepository(BaseRepository[Job]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Job)

    async def get_by_id_and_user(
        self, job_id: str | uuid.UUID, user_id: str | uuid.UUID
    ) -> Optional[Job]:
        result = await self.db.execute(
            select(Job).where(Job.id == job_id, Job.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: str | uuid.UUID,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        q = select(Job).where(Job.user_id == user_id)
        if status:
            q = q.where(Job.status == status)
        q = (
            q.order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def list_with_jd(
        self,
        user_id: str | uuid.UUID,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[tuple[Job, Optional[JobJD]]]:
        """List jobs joined with their JD in one query (outer join; JD may be
        absent). Returns (Job, JobJD|None) tuples."""
        q = (
            select(Job, JobJD)
            .outerjoin(JobJD, JobJD.job_id == Job.id)
            .where(Job.user_id == user_id)
        )
        if status:
            q = q.where(Job.status == status)
        q = (
            q.order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(q)
        return [(row[0], row[1]) for row in result.all()]

    async def count_by_user(
        self,
        user_id: str | uuid.UUID,
        status: Optional[JobStatus] = None,
    ) -> int:
        q = (
            select(func.count())
            .select_from(Job)
            .where(Job.user_id == user_id)
        )
        if status:
            q = q.where(Job.status == status)
        result = await self.db.execute(q)
        return result.scalar_one()
