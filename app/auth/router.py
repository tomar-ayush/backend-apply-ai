from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.auth.schemas import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
)
from app.auth.service import AuthService

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    req: RegisterRequest, db: AsyncSession = Depends(get_db)
):
    return await AuthService(db).register(req)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    return await AuthService(db).login(req)
