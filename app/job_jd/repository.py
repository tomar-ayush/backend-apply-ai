import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.job_jd.models import JobJD


class JobJDRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_job_id(self, job_id: str | uuid.UUID) -> Optional[JobJD]:
        result = await self.db.execute(select(JobJD).where(JobJD.job_id == job_id))
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> JobJD:
        jd = JobJD(**kwargs)
        self.db.add(jd)
        await self.db.flush()
        await self.db.refresh(jd)
        return jd

    async def update(self, jd: JobJD, **kwargs) -> JobJD:
        for key, value in kwargs.items():
            setattr(jd, key, value)
        await self.db.flush()
        await self.db.refresh(jd)
        return jd

    async def upsert(self, job_id: uuid.UUID, **kwargs) -> JobJD:
        existing = await self.get_by_job_id(job_id)
        if existing:
            return await self.update(existing, **kwargs)
        return await self.create(job_id=job_id, **kwargs)
