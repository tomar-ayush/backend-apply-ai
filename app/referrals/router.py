import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.referrals.schemas import (
    ReferralResponse,
    UpdateReferralRequest,
)
from app.referrals.service import ReferralService

router = APIRouter()


@router.delete("/{referral_id}")
async def delete_referral(
    referral_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ReferralService(db).delete(referral_id, current_user)
    return {"success": True, "message": "Referral deleted"}


@router.patch("/{referral_id}", response_model=ReferralResponse)
async def update_referral(
    referral_id: uuid.UUID,
    req: UpdateReferralRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    referral = await ReferralService(db).update(
        referral_id, req, current_user
    )
    return ReferralResponse.model_validate(referral)
