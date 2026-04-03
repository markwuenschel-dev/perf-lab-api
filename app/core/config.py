"""
app/core/config.py

Add these to your .env:

    SECRET_KEY=<run: openssl rand -hex 32>
    ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=10080   # 7 days

Render (and similar) inject DATABASE_URL as postgresql://... which loads the sync
psycopg2 driver. We normalize plain postgresql/postgres schemes to
postgresql+asyncpg for SQLAlchemy's async engine.
"""

from urllib.parse import urlparse, urlunparse

from pydantic import field_validator
from pydantic_settings import BaseSettings


def _asyncpg_database_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in ("postgresql", "postgres"):
        return urlunparse(parsed._replace(scheme="postgresql+asyncpg"))
    return url


class Settings(BaseSettings):
    PROJECT_NAME: str = "Performance Lab API"
    API_V1_STR: str = "/v1"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/dbname"
    DEBUG: bool = True

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def database_url_use_async_driver(cls, v: object) -> object:
        if isinstance(v, str):
            return _asyncpg_database_url(v)
        return v

    # Auth
    SECRET_KEY: str = "change-me-in-production"  # override in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
