import uuid
import structlog
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.models import Task, TaskStatus, TERMINAL_TASK_STATUSES, is_valid_task_transition
from app.tasks.repository import TaskRepository
from app.tasks.schemas import CreateTaskRequest, UpdateTaskRequest
from app.jobs.repository import JobRepository
from app.users.models import User
from app.common.exceptions import NotFoundError, InvalidTransitionError, ForbiddenError

logger = structlog.get_logger()


class TaskService:
    def __init__(self, db: AsyncSession):
        self.repo = TaskRepository(db)
        self.job_repo = JobRepository(db)

    async def create(self, req: CreateTaskRequest, user: User) -> Task:
        job = await self.job_repo.get_by_id_and_user(req.job_id, user.id)
        if job is None:
            raise NotFoundError("Job", str(req.job_id))

        task = await self.repo.create(
            job_id=req.job_id,
            user_id=user.id,
            task_type=req.task_type,
            payload=req.payload,
            status=TaskStatus.QUEUED,
        )
        logger.info("task_created", task_id=str(task.id), type=req.task_type.value)
        return task

    async def get(self, task_id: uuid.UUID, user: User) -> Task:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.user_id != user.id:
            raise ForbiddenError("You do not have access to this task")
        return task

    async def update(self, task_id: uuid.UUID, req: UpdateTaskRequest, user: User) -> Task:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.user_id != user.id:
            raise ForbiddenError("You do not have access to this task")

        # Idempotency: ignore updates to terminal tasks
        if task.status in TERMINAL_TASK_STATUSES:
            logger.info(
                "task_update_ignored_terminal",
                task_id=str(task_id),
                current=task.status.value,
                requested=req.status.value,
            )
            return task

        if not is_valid_task_transition(task.status, req.status):
            raise InvalidTransitionError(task.status.value, req.status.value)

        updates = {"status": req.status}
        if req.error_message is not None:
            updates["error_message"] = req.error_message

        task = await self.repo.update(task, **updates)
        logger.info("task_updated", task_id=str(task_id), status=req.status.value)
        return task
