import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.tasks.schemas import CreateTaskRequest, UpdateTaskRequest, TaskResponse, TriggerWorkdayRequest, TriggerWorkdayResponse, WorkdayCallbackRequest, TriggerLinkedinRequest, TriggerLinkedinResponse, LinkedinCallbackRequest
from app.tasks.service import TaskService

router = APIRouter()


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    req: CreateTaskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await TaskService(db).create(req, current_user)
    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await TaskService(db).get(task_id, current_user)
    return TaskResponse.model_validate(task)


@router.post("/workday/trigger", response_model=TriggerWorkdayResponse, status_code=201)
async def trigger_workday(
    req: TriggerWorkdayRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await TaskService(db).trigger_workday(req, current_user)
    return TriggerWorkdayResponse(queued=True, task_id=task.id)


@router.post("/{task_id}/callback")
async def workday_callback(
    task_id: uuid.UUID,
    req: WorkdayCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    # No user auth — validated via signed callback token from the worker
    return await TaskService(db).handle_workday_callback(task_id, req)


@router.post("/referrals/{referral_id}/connect", response_model=TriggerLinkedinResponse, status_code=201)
async def trigger_linkedin(
    referral_id: uuid.UUID,
    req: TriggerLinkedinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    referral = await TaskService(db).trigger_linkedin(referral_id, req, current_user)
    return TriggerLinkedinResponse(queued=True, referral_id=referral.id)


@router.post("/referrals/{referral_id}/callback")
async def linkedin_callback(
    referral_id: uuid.UUID,
    req: LinkedinCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    # No user auth — validated via signed callback token from the agent
    return await TaskService(db).handle_linkedin_callback(referral_id, req)


@router.delete("/{task_id}")
async def delete_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await TaskService(db).delete(task_id, current_user)
    return {"success": True, "message": "Task deleted"}
