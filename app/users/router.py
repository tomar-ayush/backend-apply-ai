from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.users.schemas import UserProfile, UpdateUserRequest
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
