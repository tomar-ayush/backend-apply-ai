import uuid
from datetime import datetime
from typing import Optional, Any, Dict, List

from pydantic import BaseModel


class JobJDResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    company: Optional[str] = None
    role: Optional[str] = None
    workday_job_id: Optional[str] = None
    raw_text: Optional[str] = None
    skills: Optional[Dict[str, Any]] = None
    keywords: Optional[List[str]] = None
    team_signals: Optional[List[str]] = None
    llm_summary: Optional[str] = None
    learning: Optional[Dict[str, List[str]]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateJDRequest(BaseModel):
    """Partial JD update. Every field is optional; only the fields the client
    actually sends are applied."""

    company: Optional[str] = None
    role: Optional[str] = None
    workday_job_id: Optional[str] = None
    raw_text: Optional[str] = None
    skills: Optional[Dict[str, Any]] = None
    keywords: Optional[List[str]] = None
    team_signals: Optional[List[str]] = None
    llm_summary: Optional[str] = None
    learning: Optional[Dict[str, List[str]]] = None
