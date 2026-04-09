"""
app/core/config.py
"""

from urllib.parse import urlparse, urlunparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _asyncpg_database_url(url: str) -> str:
    """Convert postgresql:// → postgresql+asyncpg:// for SQLAlchemy async engine."""
    parsed = urlparse(url)
    if parsed.scheme in ("postgresql", "postgres"):
        return urlunparse(parsed._replace(scheme="postgresql+asyncpg"))
    return url


class Settings(BaseSettings):
    PROJECT_NAME: str = "Performance Lab API"
    API_V1_STR: str = "/v1"

    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/dbname"
    DEBUG: bool = True

    # Auth
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Future features
    USE_STRUCTURED_COACHING_TEMPLATES: bool = True

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def database_url_use_async_driver(cls, v: object) -> object:
        if isinstance(v, str):
            return _asyncpg_database_url(v)
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",          # ignore unknown env vars
    )


settings = Settings()