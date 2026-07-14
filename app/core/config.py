"""
app/core/config.py
"""

from urllib.parse import urlparse, urlunparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The public default signing key. A production boot with this (or an empty/weak) value
# is refused by `app.main._check_production_secrets` (INT-01) — it would let anyone forge
# JWTs. Kept as a named constant so the field default and the boot guard cannot drift.
DEFAULT_SECRET_KEY = "change-me-in-production"

# The local-dev CORS origins. A production boot that only allows these (i.e. no explicit
# prod origin pinned) is refused by `app.main._check_production_cors` (INT-09). Kept as a
# named constant so the field default and the boot guard cannot drift.
DEV_DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


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

    # Deployment environment. Set ENVIRONMENT=production in real deployments so
    # safety checks (e.g. the Alembic-head check) fail fast instead of only
    # logging. Anything other than production/prod is treated as non-production.
    ENVIRONMENT: str = "development"

    # Auth
    SECRET_KEY: str = DEFAULT_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS — comma-separated list of allowed origins.
    # Defaults to local dev origins only. In production you MUST pin an explicit prod
    # origin here via ALLOWED_ORIGINS (e.g. https://perflab.44-198-76-44.nip.io) —
    # boot fails otherwise (see app.main._check_production_cors, INT-09).
    ALLOWED_ORIGINS: str = ",".join(DEV_DEFAULT_ORIGINS)

    # Regex of additional allowed origins, matched by CORSMiddleware. Disabled by
    # default (empty = no regex) — pin explicit origins via ALLOWED_ORIGINS instead.
    # Set ALLOWED_ORIGIN_REGEX explicitly only if you genuinely need pattern matching.
    ALLOWED_ORIGIN_REGEX: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_origin_regex(self) -> str | None:
        return self.ALLOWED_ORIGIN_REGEX or None

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"production", "prod"}

    # Wearable sync (Phase 2 — Oura OAuth + PAT; PDR-0006/0007).
    # OAuth client credentials from the Oura developer console; empty in dev
    # until a real app is registered. The redirect URI must match exactly what
    # is registered there and where the callback route is mounted (/v1/...).
    OURA_CLIENT_ID: str = ""
    OURA_CLIENT_SECRET: str = ""
    OURA_REDIRECT_URI: str = "http://localhost:8000/v1/integrations/oura/callback"
    # Fernet key (urlsafe-b64 32 bytes) for encrypting wearable tokens at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    APP_ENCRYPTION_KEY: str = ""
    # Where the OAuth callback redirects the browser back to after connecting.
    WEB_APP_URL: str = "http://localhost:5173"

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