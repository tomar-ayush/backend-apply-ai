import uuid
from app.common.logging import get_logger
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.common.security import encrypt, decrypt
from app.users.models import User, DEFAULT_LINKEDIN_MESSAGE
from app.users.repository import UserRepository
from app.users.schemas import UserProfile, UpdateUserRequest
from app.common.exceptions import NotFoundError, BadRequestError

logger = get_logger(__name__)

# Maps an LLM provider (or alias) to the user column that stores its
# encrypted API key.
PROVIDER_KEY_ATTR = {
    "openai": "openai_llm_api_key",
    "anthropic": "claude_llm_api_key",
    "claude": "claude_llm_api_key",
    "gemini": "gemini_llm_api_key",
    "openrouter": "openrouter_llm_api_key",
}


def get_decrypted_llm_key(user: User) -> Optional[str]:
    """Return the decrypted LLM API key for *user* without touching the DB."""
    provider = (user.llm_provider or "").lower()
    attr = PROVIDER_KEY_ATTR.get(provider)
    if not attr:
        return None
    enc = getattr(user, attr, None)
    if not enc:
        return None
    try:
        return decrypt(enc)
    except Exception:
        return enc


class UserService(BaseService):
    get_decrypted_llm_key = staticmethod(get_decrypted_llm_key)

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.repo = UserRepository(db)

    def _to_profile(self, user: User) -> UserProfile:
        return UserProfile(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            first_name=user.first_name,
            middle_name=user.middle_name,
            last_name=user.last_name,
            phone=user.phone,
            country=user.country,
            city=user.city,
            state=user.state,
            address=user.address,
            postal_code=user.postal_code,
            current_company=user.current_company,
            current_title=user.current_title,
            years_of_experience=user.years_of_experience,
            skills=user.skills,
            education=user.education,
            linkedin_message=user.linkedin_message or DEFAULT_LINKEDIN_MESSAGE,
            original_resume_pdf_url=user.original_resume_pdf_url,
            original_resume_latex_url=user.original_resume_latex_url,
            llm_provider=user.llm_provider,
            current_llm_model=user.current_llm_model,
            has_llm_api_key=bool(get_decrypted_llm_key(user)),
            has_openrouter_key=bool(user.openrouter_llm_api_key),
            has_openai_key=bool(user.openai_llm_api_key),
            has_gemini_key=bool(user.gemini_llm_api_key),
            has_claude_key=bool(user.claude_llm_api_key),
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    async def get_me(self, user: User) -> UserProfile:
        return self._to_profile(user)

    @staticmethod
    def _key_attr_for_provider(
        provider: Optional[str],
    ) -> Optional[str]:
        if not provider:
            return None
        return PROVIDER_KEY_ATTR.get(provider.lower())

    async def update_me(
        self, user: User, req: UpdateUserRequest
    ) -> UserProfile:
        updates = req.model_dump(
            exclude_unset=True, exclude={"llm_api_key"}
        )

        if req.llm_api_key is not None:
            provider = req.llm_provider or user.llm_provider
            attr = self._key_attr_for_provider(provider)
            if not attr:
                raise BadRequestError(
                    "llm_provider must be set to store an API key"
                )
            updates[attr] = encrypt(req.llm_api_key)

        updated = await self.repo.update(user, **updates)
        return self._to_profile(updated)

    async def update_linkedin_message(
        self, user: User, linkedin_message: str
    ) -> UserProfile:
        if not linkedin_message or not linkedin_message.strip():
            raise BadRequestError("LinkedIn message cannot be empty")
        updated = await self.repo.update(
            user, linkedin_message=linkedin_message.strip()
        )
        return self._to_profile(updated)
