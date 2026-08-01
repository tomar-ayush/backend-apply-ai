import uuid
import enum
from datetime import datetime
from typing import Optional, List, Set, TYPE_CHECKING

from sqlalchemy import (
    Text,
    Boolean,
    Enum as SAEnum,
    ForeignKey,
    DateTime,
    func,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base
from app.common.state_machine import StateMachine

if TYPE_CHECKING:
    from app.users.models import User
    from app.job_jd.models import JobJD
    from app.referrals.models import Referral
    from app.tasks.models import Task


class JobStatus(str, enum.Enum):
    NEW = "NEW"
    JD_PARSED = "JD_PARSED"
    REFERRAL_RECEIVED = "REFERRAL_RECEIVED"
    REFERRAL_NOT_RECEIVED = "REFERRAL_NOT_RECEIVED"
    APPLIED = "APPLIED"
    OA = "OA"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


VALID_JOB_TRANSITIONS: dict[JobStatus, Set[JobStatus]] = {
    JobStatus.NEW: {JobStatus.JD_PARSED},
    JobStatus.JD_PARSED: {
        JobStatus.REFERRAL_RECEIVED,
        JobStatus.REFERRAL_NOT_RECEIVED,
    },
    JobStatus.REFERRAL_RECEIVED: {JobStatus.APPLIED},
    JobStatus.REFERRAL_NOT_RECEIVED: {JobStatus.APPLIED},
    JobStatus.APPLIED: {
        JobStatus.OA,
        JobStatus.INTERVIEW,
        JobStatus.REJECTED,
    },
    JobStatus.OA: {JobStatus.INTERVIEW, JobStatus.REJECTED},
    JobStatus.INTERVIEW: {JobStatus.OFFER, JobStatus.REJECTED},
    JobStatus.OFFER: {JobStatus.WITHDRAWN},
    JobStatus.REJECTED: set(),
    JobStatus.WITHDRAWN: set(),
}

job_state_machine = StateMachine(VALID_JOB_TRANSITIONS)


def is_valid_job_transition(
    current: JobStatus, next_status: JobStatus
) -> bool:
    return job_state_machine.is_valid(current, next_status)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workday_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum"),
        nullable=False,
        default=JobStatus.NEW,
        index=True,
    )
    referral_received: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    optimized_resume_pdf_url: Mapped[Optional[str]] = mapped_column(
        Text
    )
    optimized_resume_latex_url: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="jobs")
    jd: Mapped[Optional["JobJD"]] = relationship(
        "JobJD",
        back_populates="job",
        cascade="all, delete-orphan",
        uselist=False,
    )
    referrals: Mapped[List["Referral"]] = relationship(
        "Referral", back_populates="job", cascade="all, delete-orphan"
    )
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="job", cascade="all, delete-orphan"
    )
