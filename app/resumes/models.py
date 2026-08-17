import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, func, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base

if TYPE_CHECKING:
    from app.users.models import User
    from app.jobs.models import Job


class LatexPackageUsage(Base):
    """Tracks LaTeX packages the compiler had to install on the fly (fallback).

    Each row is one package; `download_count` is how many times it has been pulled
    in during a compile. Upserted: first sighting -> count=1, later sightings -> +1.
    """

    __tablename__ = "latex_package_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    package_name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class ResumePreview(Base):
    """Stores the generated differences of an optimized resume before final approval."""
    __tablename__ = "resume_previews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_latex: Mapped[str] = mapped_column(Text, nullable=False)
    section_diffs: Mapped[dict] = mapped_column(JSON, nullable=False)
    extra_keywords: Mapped[Optional[list[str]]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped["Job"] = relationship("Job")
    user: Mapped["User"] = relationship("User")
