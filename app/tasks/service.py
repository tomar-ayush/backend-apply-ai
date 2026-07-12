import uuid
import httpx
from datetime import datetime, timezone
from app.common.logging import get_logger
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.models import (
    Task,
    TaskType,
    TaskStatus,
    TERMINAL_TASK_STATUSES,
    is_valid_task_transition,
)
from app.tasks.repository import TaskRepository
from app.tasks.schemas import (
    CreateTaskRequest,
    UpdateTaskRequest,
    TriggerWorkdayRequest,
    WorkdayCallbackRequest,
    TriggerLinkedinRequest,
    TriggerLinkedinResponse,
    LinkedinCallbackRequest,
)
from app.jobs.repository import JobRepository
from app.users.models import User
from app.referrals.models import Referral, ReferralStatus
from app.referrals.repository import ReferralRepository
from app.referrals.service import _normalize_linkedin_url
from app.common.exceptions import (
    NotFoundError,
    InvalidTransitionError,
    ForbiddenError,
    ExternalServiceError,
    BadRequestError,
)
from app.common.security import (
    create_callback_token,
    verify_callback_token,
)
from app.config import settings

logger = get_logger(__name__)


class TaskService:
    def __init__(self, db: AsyncSession):
        self.repo = TaskRepository(db)
        self.job_repo = JobRepository(db)
        self.referral_repo = ReferralRepository(db)

    async def create(self, req: CreateTaskRequest, user: User) -> Task:
        job = await self.job_repo.get_by_id_and_user(
            req.job_id, user.id
        )
        if job is None:
            raise NotFoundError("Job", str(req.job_id))

        task = await self.repo.create(
            job_id=req.job_id,
            user_id=user.id,
            task_type=req.task_type,
            payload=req.payload,
            status=TaskStatus.QUEUED,
        )
        logger.info(
            "task_created task_id=%s type=%s",
            str(task.id),
            req.task_type.value,
        )
        return task

    async def get(self, task_id: uuid.UUID, user: User) -> Task:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.user_id != user.id:
            raise ForbiddenError("You do not have access to this task")
        return task

    async def update(
        self, task_id: uuid.UUID, req: UpdateTaskRequest, user: User
    ) -> Task:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.user_id != user.id:
            raise ForbiddenError("You do not have access to this task")

        # Idempotency: ignore updates to terminal tasks
        if task.status in TERMINAL_TASK_STATUSES:
            logger.info(
                "task_update_ignored_terminal task_id=%s current=%s requested=%s",
                str(task_id),
                task.status.value,
                req.status.value,
            )
            return task

        if not is_valid_task_transition(task.status, req.status):
            raise InvalidTransitionError(
                task.status.value, req.status.value
            )

        updates = {"status": req.status}
        if req.error_message is not None:
            updates["error_message"] = req.error_message

        task = await self.repo.update(task, **updates)
        logger.info(
            "task_updated task_id=%s status=%s",
            str(task_id),
            req.status.value,
        )
        return task

    async def trigger_workday(
        self, req: TriggerWorkdayRequest, user: User
    ) -> Task:
        """Create a WORKDAY_APPLY task and dispatch it to the local worker.

        Mirrors the referrals connect flow: a signed callback token is issued and
        sent to the worker so it can report completion via the callback endpoint.
        """
        job = await self.job_repo.get_by_id_and_user(
            req.job_id, user.id
        )
        if job is None:
            raise NotFoundError("Job", str(req.job_id))

        task = await self.repo.create(
            job_id=req.job_id,
            user_id=user.id,
            task_type=TaskType.WORKDAY_APPLY,
            payload={
                "job_url": req.job_url,
                "resume_url": req.resume_url,
            },
            status=TaskStatus.QUEUED,
        )

        callback_token = create_callback_token(str(task.id))
        callback_url = f"{settings.API_BASE_URL.rstrip('/')}/tasks/{task.id}/callback"

        worker_payload = {
            "task_id": str(task.id),
            "application_id": task.id,
            "job_url": req.job_url,
            "resume_url": req.resume_url,
            "callback_url": callback_url,
            "callback_token": callback_token,
        }

        worker_url = req.worker_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{worker_url}/run-workday-task",
                    json=worker_payload,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ExternalServiceError(
                "Worker",
                f"Worker returned HTTP {e.response.status_code}",
            )
        except httpx.RequestError as e:
            raise ExternalServiceError(
                "Worker", f"Could not reach worker at {worker_url}: {e}"
            )

        task = await self.repo.update(task, status=TaskStatus.RUNNING)
        logger.info(
            "workday_task_triggered task_id=%s worker_url=%s",
            str(task.id),
            worker_url,
        )
        return task

    async def handle_workday_callback(
        self, task_id: uuid.UUID, req: WorkdayCallbackRequest
    ) -> dict:
        token_task_id = verify_callback_token(req.token)
        if token_task_id != str(task_id):
            raise ForbiddenError("Callback token does not match task")

        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise NotFoundError("Task", str(task_id))
        if task.task_type != TaskType.WORKDAY_APPLY:
            raise BadRequestError(
                "Task is not a Workday automation task"
            )

        if req.state == "completed":
            task = await self.repo.update(
                task, status=TaskStatus.COMPLETED
            )
            logger.info(
                "workday_callback_completed task_id=%s", str(task_id)
            )
        else:
            task = await self.repo.update(
                task, status=TaskStatus.FAILED, error_message=req.error
            )
            logger.warning(
                "workday_callback_failed task_id=%s error=%s",
                str(task_id),
                req.error,
            )

        return {"success": True, "state": req.state}

    async def trigger_linkedin(
        self,
        referral_id: uuid.UUID,
        req: TriggerLinkedinRequest,
        user: User,
    ) -> Referral:
        """Dispatch the LinkedIn connect agent for a referral.

        Mirrors the Workday trigger: a signed callback token is issued and sent to
        the agent so it can report completion. Unlike Workday, no task row is
        created — on callback we only update the referral's status
        (see handle_linkedin_callback).
        """
        referral = await self.referral_repo.get_by_id(referral_id)
        if referral is None:
            raise NotFoundError("Referral", str(referral_id))

        callback_token = create_callback_token(str(referral_id))
        callback_url = f"{settings.API_BASE_URL.rstrip('/')}/tasks/referrals/{referral_id}/callback"

        normalized_linkedin_url = _normalize_linkedin_url(
            req.linkedin_url
        )

        task = await self.repo.create(
            job_id=req.job_id,
            user_id=user.id,
            task_type=TaskType.LINKEDIN_CONNECT,
            payload={
                "referral_id": str(referral_id),
                "linkedin_url": normalized_linkedin_url,
                "referral_name": referral.name,
                "callback_url": callback_url,
            },
            status=TaskStatus.QUEUED,
        )

        payload = {
            "referral_id": str(referral_id),
            "task_id": str(task.id),
            "linkedin_url": normalized_linkedin_url,
            "message": req.message,
            "referral_name": referral.name,
            "user_name": user.full_name,
            "callback_url": callback_url,
            "callback_token": callback_token,
        }

        agent_url = req.agent_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{agent_url}/run-task", json=payload
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ExternalServiceError(
                "Agent", f"Agent returned HTTP {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise ExternalServiceError(
                "Agent", f"Could not reach agent at {agent_url}: {e}"
            )

        task = await self.repo.update(task, status=TaskStatus.RUNNING)
        logger.info(
            "linkedin_agent_task_running referral_id=%s agent_url=%s, task_id=%s",
            str(referral_id),
            agent_url,
            str(task.id),
        )
        return referral

    async def handle_linkedin_callback(
        self, referral_id: uuid.UUID, req: LinkedinCallbackRequest
    ) -> dict:
        token_referral_id = verify_callback_token(req.token)
        if token_referral_id != str(referral_id):
            raise ForbiddenError(
                "Callback token does not match referral"
            )

        referral = await self.referral_repo.get_by_id(referral_id)
        if referral is None:
            raise NotFoundError("Referral", str(referral_id))

        task = await self.repo.get_by_id(req.task_id)
        if task is None:
            raise NotFoundError("Task", str(req.task_id))

        if req.state == "completed":
            await self.repo.update(task, status=TaskStatus.COMPLETED)

            await self.referral_repo.update(
                referral,
                status=ReferralStatus.REQUESTED,
                asked_at=datetime.now(timezone.utc),
            )
            logger.info(
                "linkedin_agent_callback_completed referral_id=%s",
                str(referral_id),
            )
        else:
            logger.warning(
                "linkedin_agent_callback_failed referral_id=%s error=%s",
                str(referral_id),
                req.error,
            )

        return {"success": True, "state": req.state}
