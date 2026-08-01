import uuid
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import (
    String,
    Text,
    JSON,
    ForeignKey,
    DateTime,
    func,
    ARRAY,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base

if TYPE_CHECKING:
    from app.jobs.models import Job


class JobJD(Base):
    __tablename__ = "job_jds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    company: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(255))
    workday_job_id: Mapped[Optional[str]] = mapped_column(String(255))
    raw_html: Mapped[Optional[str]] = mapped_column(Text)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    skills: Mapped[Optional[dict]] = mapped_column(JSON)
    keywords: Mapped[Optional[dict]] = mapped_column(JSON)
    extracted_department: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String)
    )
    llm_summary: Mapped[Optional[str]] = mapped_column(Text)
    learning: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped["Job"] = relationship("Job", back_populates="jd")
