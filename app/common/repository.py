"""Generic async repository base class.

Provides type-safe CRUD operations so concrete repositories only declare
custom query methods.
"""

import uuid
from typing import Generic, TypeVar, Optional, Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Async repository with standard CRUD operations."""

    def __init__(self, db: AsyncSession, model: type[ModelT]):
        self.db = db
        self.model = model

    async def get_by_id(
        self, obj_id: str | uuid.UUID
    ) -> Optional[ModelT]:
        result = await self.db.execute(
            select(self.model).where(self.model.id == obj_id)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by=None,
    ) -> Sequence[ModelT]:
        q = select(self.model)
        if order_by is not None:
            q = q.order_by(order_by)
        q = q.limit(limit).offset(offset)
        result = await self.db.execute(q)
        return result.scalars().all()

    async def count_all(self) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(self.model)
        )
        return result.scalar_one()

    async def create(self, **kwargs) -> ModelT:
        obj = self.model(**kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **kwargs) -> ModelT:
        for key, value in kwargs.items():
            if not hasattr(obj, key):
                raise AttributeError(
                    f"{self.model.__name__} has no attribute '{key}'"
                )
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.db.delete(obj)
        await self.db.flush()
