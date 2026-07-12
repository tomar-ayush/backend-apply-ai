import uuid
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals.models import Referral, ReferralStatus
from app.common.exceptions import BadRequestError


class ReferralRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(
        self, referral_id: str | uuid.UUID
    ) -> Optional[Referral]:
        result = await self.db.execute(
            select(Referral).where(Referral.id == referral_id)
        )
        return result.scalar_one_or_none()

    async def list_by_job(
        self,
        job_id: str | uuid.UUID,
        order_by: Optional[str] = "priority",
        descending: bool = False,
    ) -> List[Referral]:
        """List referrals for a job, optionally ordered.

        `order_by` names a Referral column to sort by (defaults to "priority").
        `descending` flips the sort direction. Backward compatible: with no
        args it returns referrals ordered by priority ascending.
        """
        from sqlalchemy import asc, desc

        column = getattr(Referral, order_by, None)
        if column is None:
            raise BadRequestError(
                f"Cannot order referrals by unknown field: {order_by!r}"
            )
        stmt = (
            select(Referral)
            .where(Referral.job_id == job_id)
            .order_by(desc(column) if descending else asc(column))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs) -> Referral:
        referral = Referral(**kwargs)
        self.db.add(referral)
        await self.db.flush()
        await self.db.refresh(referral)
        return referral

    async def create_many(self, records: List[dict]) -> List[Referral]:
        referrals = [Referral(**r) for r in records]
        self.db.add_all(referrals)
        await self.db.flush()
        for r in referrals:
            await self.db.refresh(r)
        return referrals

    async def delete(self, referral: Referral) -> None:
        await self.db.delete(referral)
        await self.db.flush()

    async def update(self, referral: Referral, **kwargs) -> Referral:
        for key, value in kwargs.items():
            setattr(referral, key, value)
        await self.db.flush()
        await self.db.refresh(referral)
        return referral
