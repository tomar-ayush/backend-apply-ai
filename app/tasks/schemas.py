import uuid
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
