import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


def make_user(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password="hashed",
        full_name="Test User",
        # Workday profile
        first_name=None,
        middle_name=None,
        last_name=None,
        phone=None,
        country=None,
        city=None,
        state=None,
        address=None,
        postal_code=None,
        # Professional profile
        current_company=None,
        current_title=None,
        years_of_experience=None,
        skills=None,
        education=None,
        # Resume storage
        original_resume_pdf_url=None,
        original_resume_latex_url=None,
        ai_resume_latex_url=None,
        # LLM config
        llm_provider="openai",
        encrypted_llm_api_key=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    user = MagicMock()
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def make_job(**kwargs):
    from app.jobs.models import JobStatus
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        company="Acme Corp",
        role="Software Engineer",
        workday_url="https://acme.wd5.myworkdayjobs.com/jobs/123",
        status=JobStatus.NEW,
        optimized_resume_pdf_url=None,
        optimized_resume_latex_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    job = MagicMock()
    for k, v in defaults.items():
        setattr(job, k, v)
    return job


def make_referral(**kwargs):
    from app.referrals.models import ReferralStatus
    defaults = dict(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        name="Jane Doe",
        linkedin_url="https://linkedin.com/in/janedoe",
        status=ReferralStatus.NOT_CONTACTED,
        priority=5,
        asked_at=None,
        responded_at=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    ref = MagicMock()
    for k, v in defaults.items():
        setattr(ref, k, v)
    return ref


def make_task(**kwargs):
    from app.tasks.models import TaskStatus, TaskType
    defaults = dict(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        task_type=TaskType.LINKEDIN_CONNECT,
        payload={},
        status=TaskStatus.QUEUED,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    task = MagicMock()
    for k, v in defaults.items():
        setattr(task, k, v)
    return task
