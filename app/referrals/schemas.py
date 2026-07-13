import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from app.referrals.models import ReferralStatus


class ReferralResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    name: str
    linkedin_url: Optional[str] = None
    status: ReferralStatus
    priority: int
    asked_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateReferralRequest(BaseModel):
    status: ReferralStatus
    linkedin_url: Optional[str] = None


class GenerateReferralsResponse(BaseModel):
    generated: int
    referrals: list[ReferralResponse]
