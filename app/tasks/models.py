import uuid
import enum
from datetime import datetime
from typing import Optional, Set, TYPE_CHECKING

from sqlalchemy import (
    String,
    Text,
    Enum as SAEnum,
    ForeignKey,
    DateTime,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database.session import Base
from app.common.state_machine import StateMachine

if TYPE_CHECKING:
    from app.jobs.models import Job
    from app.users.models import User


class TaskType(str, enum.Enum):
    LINKEDIN_CONNECT = "LINKEDIN_CONNECT"
    WORKDAY_APPLY = "WORKDAY_APPLY"


class TaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_TASK_STATUSES: Set[TaskStatus] = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
}

VALID_TASK_TRANSITIONS: dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_USER,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.WAITING_USER: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}

task_state_machine = StateMachine(
    VALID_TASK_TRANSITIONS, terminal=TERMINAL_TASK_STATUSES
)


def is_valid_task_transition(
    current: TaskStatus, next_status: TaskStatus
) -> bool:
    return task_state_machine.is_valid(current, next_status)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_type: Mapped[TaskType] = mapped_column(
        SAEnum(TaskType, name="task_type_enum"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status_enum"),
        nullable=False,
        default=TaskStatus.QUEUED,
        index=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    job: Mapped["Job"] = relationship("Job", back_populates="tasks")
    user: Mapped["User"] = relationship("User", back_populates="tasks")
