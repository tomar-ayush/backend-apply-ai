import uuid
import structlog
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User
from app.users.repository import UserRepository
from app.users.schemas import UserProfile, UpdateUserRequest, ResumeUploadResponse
from app.common.security import encrypt, decrypt
from app.common.exceptions import NotFoundError
from app.storage.r2 import r2_storage

logger = structlog.get_logger()


class UserService:
    def __init__(self, db: Optional[AsyncSession]):
        self._db = db
        self._repo: Optional[UserRepository] = None

    @property
    def repo(self) -> UserRepository:
        if self._repo is None:
            self._repo = UserRepository(self._db)
        return self._repo

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
            original_resume_pdf_url=user.original_resume_pdf_url,
            original_resume_latex_url=user.original_resume_latex_url,
            llm_provider=user.llm_provider,
            has_llm_api_key=bool(user.encrypted_llm_api_key),
            has_google_search_api_key=bool(user.encrypted_google_search_api_key),
            google_search_engine_id=user.google_search_engine_id,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    async def get_me(self, user: User) -> UserProfile:
        return self._to_profile(user)

    async def update_me(self, user: User, req: UpdateUserRequest) -> UserProfile:
        updates = req.model_dump(exclude_none=True, exclude={"llm_api_key", "google_search_api_key"})

        if req.llm_api_key is not None:
            updates["encrypted_llm_api_key"] = encrypt(req.llm_api_key)
        if req.google_search_api_key is not None:
            updates["encrypted_google_search_api_key"] = encrypt(req.google_search_api_key)

        updated = await self.repo.update(user, **updates)
        return self._to_profile(updated)

    async def upload_resume(
        self,
        user: User,
        pdf_file: Optional[UploadFile],
        latex_file: Optional[UploadFile],
    ) -> ResumeUploadResponse:
        updates = {}
        pdf_url = None
        latex_url = None

        if pdf_file:
            data = await pdf_file.read()
            key = f"users/{user.id}/original_resume.pdf"
            pdf_url = r2_storage.upload_bytes(key, data, "application/pdf")
            updates["original_resume_pdf_url"] = pdf_url

        if latex_file:
            data = await latex_file.read()
            key = f"users/{user.id}/original_resume.tex"
            latex_url = r2_storage.upload_bytes(key, data, "text/x-tex")
            updates["original_resume_latex_url"] = latex_url

        if updates:
            await self.repo.update(user, **updates)

        logger.info("resume_uploaded", user_id=str(user.id), has_pdf=bool(pdf_url), has_latex=bool(latex_url))
        return ResumeUploadResponse(
            pdf_url=pdf_url,
            latex_url=latex_url,
            message="Resume uploaded successfully",
        )

    async def get_resume(self, user: User) -> ResumeUploadResponse:
        return ResumeUploadResponse(
            pdf_url=user.original_resume_pdf_url,
            latex_url=user.original_resume_latex_url,
            message="Original resume",
        )

    def get_decrypted_llm_key(self, user: User) -> Optional[str]:
        if not user.encrypted_llm_api_key:
            return None
        return decrypt(user.encrypted_llm_api_key)

    def get_decrypted_google_key(self, user: User) -> Optional[str]:
        if not user.encrypted_google_search_api_key:
            return None
        return decrypt(user.encrypted_google_search_api_key)
