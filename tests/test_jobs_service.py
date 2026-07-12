import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.models import JobStatus
from app.jobs.service import JobService
from app.common.exceptions import (
    InvalidTransitionError,
    ForbiddenError,
    NotFoundError,
)
from tests.conftest import make_user, make_job


@pytest.mark.asyncio
async def test_get_raises_not_found():
    db = AsyncMock()
    user = make_user()
    with patch("app.jobs.service.JobRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=None)
        MockRepo.return_value = mock_repo
        svc = JobService(db)
        with pytest.raises(NotFoundError):
            await svc.get(uuid.uuid4(), user)


@pytest.mark.asyncio
async def test_get_raises_forbidden_for_wrong_user():
    db = AsyncMock()
    user = make_user()
    job = make_job(user_id=uuid.uuid4())  # different user
    with patch("app.jobs.service.JobRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=job)
        MockRepo.return_value = mock_repo
        svc = JobService(db)
        with pytest.raises(ForbiddenError):
            await svc.get(job.id, user)


@pytest.mark.asyncio
async def test_update_status_valid_transition():
    db = AsyncMock()
    user = make_user()
    job = make_job(user_id=user.id, status=JobStatus.NEW)
    with patch("app.jobs.service.JobRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=job)
        mock_repo.update = AsyncMock(return_value=job)
        MockRepo.return_value = mock_repo
        svc = JobService(db)
        result = await svc.update_status(
            job.id, JobStatus.JD_PARSED, user
        )
        mock_repo.update.assert_called_once_with(
            job, status=JobStatus.JD_PARSED
        )
        assert result is job


@pytest.mark.asyncio
async def test_update_status_invalid_transition_raises():
    db = AsyncMock()
    user = make_user()
    job = make_job(user_id=user.id, status=JobStatus.NEW)
    with patch("app.jobs.service.JobRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=job)
        MockRepo.return_value = mock_repo
        svc = JobService(db)
        with pytest.raises(InvalidTransitionError):
            await svc.update_status(job.id, JobStatus.APPLIED, user)


@pytest.mark.asyncio
async def test_update_status_terminal_to_terminal_raises():
    db = AsyncMock()
    user = make_user()
    job = make_job(user_id=user.id, status=JobStatus.REJECTED)
    with patch("app.jobs.service.JobRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=job)
        MockRepo.return_value = mock_repo
        svc = JobService(db)
        with pytest.raises(InvalidTransitionError):
            await svc.update_status(job.id, JobStatus.APPLIED, user)


def test_valid_job_transitions():
    from app.jobs.models import (
        VALID_JOB_TRANSITIONS,
        is_valid_job_transition,
    )

    assert is_valid_job_transition(JobStatus.NEW, JobStatus.JD_PARSED)
    assert not is_valid_job_transition(JobStatus.NEW, JobStatus.APPLIED)
    assert is_valid_job_transition(
        JobStatus.JD_PARSED, JobStatus.REFERRAL_IN_PROGRESS
    )
    assert is_valid_job_transition(
        JobStatus.JD_PARSED, JobStatus.RESUME_GENERATED
    )
    assert is_valid_job_transition(JobStatus.APPLIED, JobStatus.OA)
    assert is_valid_job_transition(
        JobStatus.APPLIED, JobStatus.REJECTED
    )
    assert not is_valid_job_transition(
        JobStatus.REJECTED, JobStatus.APPLIED
    )
    assert not is_valid_job_transition(
        JobStatus.OFFER, JobStatus.INTERVIEW
    )
