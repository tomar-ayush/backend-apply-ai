import uuid
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals.models import Referral, ReferralStatus


class ReferralRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, referral_id: str | uuid.UUID) -> Optional[Referral]:
        result = await self.db.execute(select(Referral).where(Referral.id == referral_id))
        return result.scalar_one_or_none()

    async def list_by_job(self, job_id: str | uuid.UUID) -> List[Referral]:
        result = await self.db.execute(
            select(Referral)
            .where(Referral.job_id == job_id)
        )
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

    async def update(self, referral: Referral, **kwargs) -> Referral:
        for key, value in kwargs.items():
            setattr(referral, key, value)
        await self.db.flush()
        await self.db.refresh(referral)
        return referral
