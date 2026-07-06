from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.common.exceptions import AppException
from app.common.logging import configure_logging, get_logger
from app.auth.router import router as auth_router
from app.users.router import router as users_router
from app.jobs.router import router as jobs_router
from app.referrals.router import router as referrals_router
from app.tasks.router import router as tasks_router

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="AI Job Application API",
    description="Backend service for AI-assisted job applications",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.warning("app_exception status_code=%s detail=%s", exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception %s", str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
app.include_router(referrals_router, prefix="/referrals", tags=["referrals"])
app.include_router(tasks_router, prefix="/tasks", tags=["tasks"])


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}
