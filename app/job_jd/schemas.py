import uuid
from datetime import datetime
from typing import Optional, Any, Dict, List

from pydantic import BaseModel


class JobJDResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    raw_text: Optional[str] = None
    skills: Optional[Dict[str, Any]] = None
    keywords: Optional[List[str]] = None
    team_signals: Optional[Dict[str, Any]] = None
    llm_summary: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
