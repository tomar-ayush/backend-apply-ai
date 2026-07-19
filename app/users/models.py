import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import String, Text, JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Workday profile
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    middle_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    country: Mapped[Optional[str]] = mapped_column(String(100))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(100))
    address: Mapped[Optional[str]] = mapped_column(String(500))
    postal_code: Mapped[Optional[str]] = mapped_column(String(20))

    # Professional profile
    current_company: Mapped[Optional[str]] = mapped_column(String(255))
    current_title: Mapped[Optional[str]] = mapped_column(String(255))
    years_of_experience: Mapped[Optional[int]] = mapped_column()
    skills: Mapped[Optional[dict]] = mapped_column(JSON)
    education: Mapped[Optional[dict]] = mapped_column(JSON)

    # Resume storage (Cloudflare R2 keys)
    original_resume_latex_url: Mapped[Optional[str]] = mapped_column(
        Text
    )
    ai_resume_latex_url: Mapped[Optional[str]] = mapped_column(Text)
    original_resume_pdf_url: Mapped[Optional[str]] = mapped_column(Text)
    ai_resume_pdf_url: Mapped[Optional[str]] = mapped_column(Text)

    # LLM configuration (per-provider encrypted API keys)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(50))
    current_llm_model: Mapped[Optional[str]] = mapped_column(
        String(100)
    )
    openrouter_llm_api_key: Mapped[Optional[str]] = mapped_column(Text)
    openai_llm_api_key: Mapped[Optional[str]] = mapped_column(Text)
    gemini_llm_api_key: Mapped[Optional[str]] = mapped_column(Text)
    claude_llm_api_key: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    jobs: Mapped[List["Job"]] = relationship(
        "Job", back_populates="user", cascade="all, delete-orphan"
    )
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="user", cascade="all, delete-orphan"
    )


from app.jobs.models import Job
from app.tasks.models import Task
