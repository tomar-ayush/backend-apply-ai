import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tasks.models import TaskStatus, TaskType
from app.tasks.schemas import CreateTaskRequest, UpdateTaskRequest
from app.tasks.service import TaskService
from app.common.exceptions import (
    InvalidTransitionError,
    NotFoundError,
    ForbiddenError,
)
from tests.conftest import make_user, make_job, make_task


def test_valid_task_transitions():
    from app.tasks.models import is_valid_task_transition

    assert is_valid_task_transition(
        TaskStatus.QUEUED, TaskStatus.RUNNING
    )
    assert is_valid_task_transition(
        TaskStatus.QUEUED, TaskStatus.FAILED
    )
    assert is_valid_task_transition(
        TaskStatus.RUNNING, TaskStatus.COMPLETED
    )
    assert is_valid_task_transition(
        TaskStatus.RUNNING, TaskStatus.WAITING_USER
    )
    assert is_valid_task_transition(
        TaskStatus.WAITING_USER, TaskStatus.RUNNING
    )
    assert not is_valid_task_transition(
        TaskStatus.COMPLETED, TaskStatus.RUNNING
    )
    assert not is_valid_task_transition(
        TaskStatus.FAILED, TaskStatus.RUNNING
    )
    assert not is_valid_task_transition(
        TaskStatus.QUEUED, TaskStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_get_task_not_found():
    db = AsyncMock()
    user = make_user()
    with patch("app.tasks.service.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=None)
        MockRepo.return_value = mock_repo
        svc = TaskService(db)
        with pytest.raises(NotFoundError):
            await svc.get(uuid.uuid4(), user)


@pytest.mark.asyncio
async def test_get_task_forbidden_for_wrong_user():
    db = AsyncMock()
    user = make_user()
    task = make_task(user_id=uuid.uuid4())
    with patch("app.tasks.service.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=task)
        MockRepo.return_value = mock_repo
        svc = TaskService(db)
        with pytest.raises(ForbiddenError):
            await svc.get(task.id, user)


@pytest.mark.asyncio
async def test_update_task_terminal_is_idempotent():
    """Updates to COMPLETED/FAILED tasks must return existing state without error."""
    db = AsyncMock()
    user = make_user()
    task = make_task(user_id=user.id, status=TaskStatus.COMPLETED)
    with patch("app.tasks.service.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=task)
        MockRepo.return_value = mock_repo
        svc = TaskService(db)
        req = UpdateTaskRequest(status=TaskStatus.RUNNING)
        result = await svc.update(task.id, req, user)
        mock_repo.update.assert_not_called()
        assert result.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_update_task_invalid_transition():
    db = AsyncMock()
    user = make_user()
    task = make_task(user_id=user.id, status=TaskStatus.QUEUED)
    with patch("app.tasks.service.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=task)
        MockRepo.return_value = mock_repo
        svc = TaskService(db)
        req = UpdateTaskRequest(status=TaskStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            await svc.update(task.id, req, user)


@pytest.mark.asyncio
async def test_update_task_valid_transition():
    db = AsyncMock()
    user = make_user()
    task = make_task(user_id=user.id, status=TaskStatus.QUEUED)
    updated = make_task(user_id=user.id, status=TaskStatus.RUNNING)
    with patch("app.tasks.service.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=task)
        mock_repo.update = AsyncMock(return_value=updated)
        MockRepo.return_value = mock_repo
        svc = TaskService(db)
        req = UpdateTaskRequest(status=TaskStatus.RUNNING)
        result = await svc.update(task.id, req, user)
        mock_repo.update.assert_called_once()
        assert result.status == TaskStatus.RUNNING
