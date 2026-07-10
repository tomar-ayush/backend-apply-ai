from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List
import secrets
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        enable_decoding=False,
        extra="ignore",
    )
    APP_ENV: str = "development"
    SECRET_KEY: str
    ENCRYPTION_KEY: str

    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    R2_ACCOUNT_ID: str
    R2_ACCESS_KEY_ID: str
    R2_SECRET_ACCESS_KEY: str
    R2_BUCKET_NAME: str
    R2_PUBLIC_URL: str

    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    ALLOWED_ORIGINS: List[str]

    API_BASE_URL: str = "http://localhost:8000"
    LATEX_COMPILE_URL: str

    # DATABASE_URL normalization removed from Settings model —
    # normalization is handled in the DB session setup so the raw env value
    # remains a plain string here.

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
