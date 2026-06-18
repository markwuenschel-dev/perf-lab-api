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

    # CORS — comma-separated list of allowed origins.
    # Defaults to local dev origins only. Override via ALLOWED_ORIGINS env var
    # (e.g. add a custom production domain here).
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Regex of additional allowed origins, matched by CORSMiddleware. Defaults to
    # any Netlify site (production + deploy previews), since the web frontend is
    # deployed there. Override via ALLOWED_ORIGIN_REGEX; set to "" to disable.
    ALLOWED_ORIGIN_REGEX: str = r"https://.*\.netlify\.app"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_origin_regex(self) -> str | None:
        return self.ALLOWED_ORIGIN_REGEX or None

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