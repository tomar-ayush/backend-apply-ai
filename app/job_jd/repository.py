import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.job_jd.models import JobJD


class JobJDRepository(BaseRepository[JobJD]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, JobJD)

    async def get_by_job_id(
        self, job_id: str | uuid.UUID
    ) -> Optional[JobJD]:
        result = await self.db.execute(
            select(JobJD).where(JobJD.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, job_id: uuid.UUID, **kwargs) -> JobJD:
        existing = await self.get_by_job_id(job_id)
        if existing:
            return await self.update(existing, **kwargs)
        return await self.create(job_id=job_id, **kwargs)
