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

# Every SECRET_KEY this repository publishes. `.env.example` is documentation, so anything
# it ships is public and can never sign production tokens — including values that are
# neither the sentinel above nor short enough to trip the length floor.
#
# INT-A1: the guard used to enumerate exactly one bad key (DEFAULT_SECRET_KEY), so the
# example file's key — 57 chars, not the sentinel — booted production clean. An
# enumeration of one is what let a *different* public key through, so this set is the
# enumeration made complete.
#
# The runtime cannot simply read `.env.example` (the Dockerfile does not ship it), so
# `tests/test_production_boot_guards.py` pins this set to that file's actual contents:
# editing the file without updating this constant fails CI. Same anti-drift contract as
# the constants above — stated as a test rather than a comment.
PUBLIC_EXAMPLE_SECRET_KEYS = frozenset(
    {
        DEFAULT_SECRET_KEY,
        "change-me-in-production-generate-with-openssl-rand-hex-32",
    }
)

# The local-dev CORS origins. A production boot that only allows these (i.e. no explicit
# prod origin pinned) is refused by `app.main._check_production_cors` (INT-09). Kept as a
# named constant so the field default and the boot guard cannot drift.
DEV_DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

# The CORS spec's two magic non-origin values. Neither is a real origin, and allowing
# either in production hands out credentialed access to callers you did not pin:
#
#   `*`     — Starlette sets allow_all_origins on exact membership, and with
#             allow_credentials=True reflects the caller's Origin back.
#   `null`  — the origin of a sandboxed iframe, a data: URL, or a redirected request. An
#             attacker gets it for free with `<iframe sandbox srcdoc=...>`, so allowing
#             `null` is allowing them.
#
# Unlike PUBLIC_EXAMPLE_SECRET_KEYS (an open set that needs a test to stay complete), the
# spec defines exactly these two — the enumeration is closed, so enumerating is the right
# shape here rather than a sentinel check waiting for a third member.
CORS_NON_ORIGINS = frozenset({"*", "null"})

# INT-A1: production does not accept regex-based origin matching at all.
#
# "Is this pattern narrow enough?" has no reliable answer. A pattern can look anchored and
# still admit more than its author intended, and any check that tries to judge one from
# its syntax — or by probing it with sample origins — is a floor, not a proof: it clears
# the shapes you thought of and stays quiet about the rest. That is the same
# accept-unless-recognised idiom this whole change removes, so re-introducing it here to
# police the regex would be self-defeating.
#
# Requiring an explicit, enumerated origin makes the question decidable instead. This
# costs nothing: the setting already defaults to disabled, nothing in the repo or the
# deployment configures it, and the INT-09 decision already held that a regex alone never
# satisfies the explicit-origin requirement. Production now enforces what that decision
# already said. Non-production is unaffected — a regex there only warns, like every other
# check here.
#
# If a future deployment genuinely needs pattern matching (a fleet of preview domains,
# say), that is a real design decision with a real threat model, and it should arrive as
# its own ADR — not as a boolean someone flips at 2am.
CORS_REGEX_UNSUPPORTED_IN_PRODUCTION = (
    "ALLOWED_ORIGIN_REGEX is set, and production does not accept regex origin matching. "
    "A pattern that looks anchored can still admit origins you did not intend, and that "
    "cannot be verified from the pattern alone. Pin explicit origins via ALLOWED_ORIGINS "
    "instead, e.g. set ALLOWED_ORIGINS=https://perflab.44-198-76-44.nip.io."
)


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

    # Regex of additional allowed origins, matched by CORSMiddleware. Disabled by default
    # (empty = no regex), and REFUSED ENTIRELY in production (INT-A1) — pin explicit
    # origins via ALLOWED_ORIGINS instead. Usable in local dev only, where it warns.
    # See CORS_REGEX_UNSUPPORTED_IN_PRODUCTION for why the class is refused rather than
    # pattern-checked.
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