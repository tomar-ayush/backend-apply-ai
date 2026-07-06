"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100)),
        sa.Column("middle_name", sa.String(100)),
        sa.Column("last_name", sa.String(100)),
        sa.Column("phone", sa.String(50)),
        sa.Column("country", sa.String(100)),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(100)),
        sa.Column("address", sa.String(500)),
        sa.Column("postal_code", sa.String(20)),
        sa.Column("current_company", sa.String(255)),
        sa.Column("current_title", sa.String(255)),
        sa.Column("years_of_experience", sa.Integer()),
        sa.Column("skills", postgresql.JSON()),
        sa.Column("education", postgresql.JSON()),
        sa.Column("original_resume_pdf_url", sa.Text()),
        sa.Column("original_resume_latex_url", sa.Text()),
        sa.Column("llm_provider", sa.String(50)),
        sa.Column("encrypted_llm_api_key", sa.Text()),
        sa.Column("encrypted_google_search_api_key", sa.Text()),
        sa.Column("google_search_engine_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    job_status_enum = postgresql.ENUM(
        "NEW", "JD_PARSED", "REFERRAL_IN_PROGRESS", "WAITING_FOR_REFERRAL",
        "REFERRAL_RECEIVED", "RESUME_GENERATED", "READY_TO_APPLY", "WORKDAY_RUNNING",
        "APPLIED", "OA", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN",
        name="job_status_enum",
    )
    job_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.String(255)),
        sa.Column("role", sa.String(255)),
        sa.Column("workday_job_id", sa.String(255)),
        sa.Column("workday_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("NEW", "JD_PARSED", "REFERRAL_IN_PROGRESS", "WAITING_FOR_REFERRAL",
                                    "REFERRAL_RECEIVED", "RESUME_GENERATED", "READY_TO_APPLY", "WORKDAY_RUNNING",
                                    "APPLIED", "OA", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN",
                                    name="job_status_enum", create_type=False), nullable=False),
        sa.Column("optimized_resume_pdf_url", sa.Text()),
        sa.Column("optimized_resume_latex_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_jds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_html", sa.Text()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("skills", postgresql.JSON()),
        sa.Column("keywords", postgresql.JSON()),
        sa.Column("team_signals", postgresql.JSON()),
        sa.Column("llm_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_job_jds_job_id", "job_jds", ["job_id"], unique=True)

    referral_status_enum = postgresql.ENUM(
        "NOT_CONTACTED", "REQUESTED", "RESPONDED", "REFERRED", "DECLINED",
        name="referral_status_enum",
    )
    referral_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "referrals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("linkedin_url", sa.Text()),
        sa.Column("status", sa.Enum("NOT_CONTACTED", "REQUESTED", "RESPONDED", "REFERRED", "DECLINED",
                                    name="referral_status_enum", create_type=False), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True)),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_referrals_job_id", "referrals", ["job_id"])

    task_type_enum = postgresql.ENUM(
        "LINKEDIN_CONNECT", "WORKDAY_APPLY",
        name="task_type_enum",
    )
    task_type_enum.create(op.get_bind(), checkfirst=True)

    task_status_enum = postgresql.ENUM(
        "QUEUED", "RUNNING", "WAITING_USER", "COMPLETED", "FAILED",
        name="task_status_enum",
    )
    task_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.Enum("LINKEDIN_CONNECT", "WORKDAY_APPLY", name="task_type_enum", create_type=False), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Enum("QUEUED", "RUNNING", "WAITING_USER", "COMPLETED", "FAILED",
                                    name="task_status_enum", create_type=False), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_job_id", "tasks", ["job_id"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    op.drop_table("tasks")
    op.execute("DROP TYPE task_status_enum")
    op.execute("DROP TYPE task_type_enum")
    op.drop_table("referrals")
    op.execute("DROP TYPE referral_status_enum")
    op.drop_table("job_jds")
    op.drop_table("jobs")
    op.execute("DROP TYPE job_status_enum")
    op.drop_table("users")
