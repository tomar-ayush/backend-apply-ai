import uuid
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.tasks.models import Task, TaskStatus


class TaskRepository(BaseRepository[Task]):
    def __init__(self, db: AsyncSession):
        super().__init__(db, Task)

    async def list_by_job(self, job_id: str | uuid.UUID) -> List[Task]:
        result = await self.db.execute(
            select(Task)
            .where(Task.job_id == job_id)
            .order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_user(
        self, user_id: str | uuid.UUID
    ) -> List[Task]:
        result = await self.db.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())
