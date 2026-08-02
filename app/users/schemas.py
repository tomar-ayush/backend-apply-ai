import uuid
from datetime import datetime
from typing import Optional, Any, Dict, Literal

from pydantic import BaseModel, EmailStr, field_validator

SUPPORTED_LLM_PROVIDERS = {
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "google",
    "openrouter",
}


DEFAULT_LINKEDIN_MESSAGE = (
    "I'm exploring opportunities and would love to connect"
)


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
    linkedin_message: str = DEFAULT_LINKEDIN_MESSAGE

    # # Resume
    # original_resume_pdf_url: Optional[str] = None
    # original_resume_latex_url: Optional[str] = None

    # LLM config (keys never returned)
    llm_provider: Optional[str] = None
    current_llm_model: Optional[str] = None
    has_llm_api_key: bool = False
    has_openrouter_key: bool = False
    has_openai_key: bool = False
    has_gemini_key: bool = False
    has_claude_key: bool = False

    # created_at: datetime
    # updated_at: datetime

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
    linkedin_message: Optional[str] = None

    llm_provider: Optional[str] = None
    current_llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None

    @field_validator("llm_provider")
    @classmethod
    def validate_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.lower() not in SUPPORTED_LLM_PROVIDERS:
            raise ValueError(
                f"llm_provider must be one of: {', '.join(sorted(SUPPORTED_LLM_PROVIDERS))}"
            )
        return v.lower() if v else v


class UpdateLinkedinMessageRequest(BaseModel):
    linkedin_message: str
