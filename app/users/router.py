from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.users.schemas import UserProfile, UpdateUserRequest, ResumeUploadResponse
from app.users.service import UserService

router = APIRouter()


@router.get("/me", response_model=UserProfile)
async def get_me(current_user: User = Depends(get_current_user)):
    return await UserService(None).get_me(current_user)


@router.patch("/me", response_model=UserProfile)
async def update_me(
    req: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await UserService(db).update_me(current_user, req)


@router.post("/resume", response_model=ResumeUploadResponse)
async def upload_resume(
    pdf_file: Optional[UploadFile] = File(None),
    latex_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.common.exceptions import BadRequestError
    if pdf_file is None and latex_file is None:
        raise BadRequestError("At least one file (pdf_file or latex_file) must be provided")
    return await UserService(db).upload_resume(current_user, pdf_file, latex_file)


@router.get("/resume", response_model=ResumeUploadResponse)
async def get_resume(current_user: User = Depends(get_current_user)):
    return await UserService(None).get_resume(current_user)
