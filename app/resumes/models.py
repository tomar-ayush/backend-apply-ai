import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base


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
