import uuid
from enum import Enum
from datetime import datetime
from typing import Optional, Any, Dict

from pydantic import BaseModel

from app.tasks.models import TaskType, TaskStatus


class CreateTaskRequest(BaseModel):
    job_id: uuid.UUID
    task_type: TaskType
    payload: Dict[str, Any] = {}


class UpdateTaskRequest(BaseModel):
    status: TaskStatus
    error_message: Optional[str] = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    user_id: uuid.UUID
    task_type: TaskType
    payload: Dict[str, Any]
    status: TaskStatus
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class stateEnum(str, Enum):
    completed = "completed"
    failed = "failed"


class TriggerWorkdayRequest(BaseModel):
    """Fields supplied by the frontend to launch a local Workday automation."""

    job_id: uuid.UUID
    job_url: str
    resume_url: str
    worker_url: (
        str  # Cloudflare tunnel / local URL of the Workday worker
    )


class TriggerWorkdayResponse(BaseModel):
    queued: bool
    task_id: uuid.UUID


class WorkdayCallbackRequest(BaseModel):
    """Posted back by the worker once the automation finishes."""

    state: stateEnum
    error: Optional[str] = None
    token: str


class TriggerLinkedinRequest(BaseModel):
    """Fields supplied by the frontend to launch the LinkedIn connect agent for a referral."""

    linkedin_url: str
    message: str
    task_id: Optional[uuid.UUID] = None
class TriggerLinkedinResponse(BaseModel):
    queued: bool
    referral_id: uuid.UUID
    task_id: Optional[uuid.UUID] = None


class CompleteLinkedinRequest(BaseModel):
    """Posted by the extension using Bearer Token authentication once the connection task finishes."""

    state: str
    task_id: Optional[str] = None
    error: Optional[str] = None

