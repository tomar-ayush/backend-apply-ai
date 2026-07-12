from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
)
from app.users.repository import UserRepository
from app.common.security import (
    hash_password,
    verify_password,
    create_access_token,
)
from app.common.exceptions import ConflictError, UnauthorizedError
from app.common.logging import get_logger

logger = get_logger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def register(self, req: RegisterRequest) -> TokenResponse:
        existing = await self.repo.get_by_email(req.email)
        if existing:
            raise ConflictError("Email already registered")

        user = await self.repo.create(
            email=req.email,
            hashed_password=hash_password(req.password),
            full_name=req.full_name,
        )
        logger.info(
            "user_registered user_id=%s email=%s",
            str(user.id),
            user.email,
        )
        token = create_access_token(str(user.id))
        return TokenResponse(access_token=token)

    async def login(self, req: LoginRequest) -> TokenResponse:
        user = await self.repo.get_by_email(req.email)
        if user is None or not verify_password(
            req.password, user.hashed_password
        ):
            raise UnauthorizedError("Invalid email or password")

        logger.info("user_login user_id=%s", str(user.id))
        token = create_access_token(str(user.id))
        return TokenResponse(access_token=token)
