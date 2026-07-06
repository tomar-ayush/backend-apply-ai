import uuid
import enum
from datetime import datetime
from typing import Optional, Set

from sqlalchemy import String, Text, Enum as SAEnum, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base


class ReferralStatus(str, enum.Enum):
    NOT_CONTACTED = "NOT_CONTACTED"
    REQUESTED = "REQUESTED"
    RESPONDED = "RESPONDED"
    REFERRED = "REFERRED"
    DECLINED = "DECLINED"


VALID_REFERRAL_TRANSITIONS: dict[ReferralStatus, Set[ReferralStatus]] = {
    ReferralStatus.NOT_CONTACTED: {ReferralStatus.REQUESTED},
    ReferralStatus.REQUESTED: {ReferralStatus.RESPONDED, ReferralStatus.DECLINED},
    ReferralStatus.RESPONDED: {ReferralStatus.REFERRED, ReferralStatus.DECLINED},
    ReferralStatus.REFERRED: set(),
    ReferralStatus.DECLINED: set(),
}


def is_valid_referral_transition(current: ReferralStatus, next_status: ReferralStatus) -> bool:
    return next_status in VALID_REFERRAL_TRANSITIONS.get(current, set())


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ReferralStatus] = mapped_column(
        SAEnum(ReferralStatus, name="referral_status_enum"),
        nullable=False,
        default=ReferralStatus.NOT_CONTACTED,
    )
    priority: Mapped[Optional[int]] = mapped_column(default=5)
    asked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped["Job"] = relationship("Job", back_populates="referrals")


from app.jobs.models import Job
