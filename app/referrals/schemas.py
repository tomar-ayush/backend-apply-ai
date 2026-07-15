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


class CreateReferralRequest(BaseModel):
    """A single user-provided referral to persist for a job."""

    name: str
    linkedin_url: Optional[str] = None
    priority: int = 5


class BulkCreateReferralsRequest(BaseModel):
    """A list of user-provided referrals to persist for a job."""

    referrals: List[CreateReferralRequest]


class GenerateReferralsResponse(BaseModel):
    generated: int
    referrals: list[ReferralResponse]
