import uuid
from datetime import datetime
from typing import Optional, Any, Dict

from pydantic import BaseModel, EmailStr, HttpUrl, model_validator


class UserProfile(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str

    # Workday profile
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None

    # Professional profile
    current_company: Optional[str] = None
    current_title: Optional[str] = None
    years_of_experience: Optional[int] = None
    skills: Optional[Dict[str, Any]] = None
    education: Optional[Dict[str, Any]] = None

    # Resume
    original_resume_pdf_url: Optional[str] = None
    original_resume_latex_url: Optional[str] = None

    # LLM config (keys never returned)
    llm_provider: Optional[str] = None
    has_llm_api_key: bool = False
    has_google_search_api_key: bool = False
    google_search_engine_id: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None

    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None

    current_company: Optional[str] = None
    current_title: Optional[str] = None
    years_of_experience: Optional[int] = None
    skills: Optional[Dict[str, Any]] = None
    education: Optional[Dict[str, Any]] = None

    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    google_search_api_key: Optional[str] = None
    google_search_engine_id: Optional[str] = None


class ResumeUploadResponse(BaseModel):
    pdf_url: Optional[str] = None
    latex_url: Optional[str] = None
    message: str
