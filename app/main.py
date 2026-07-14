"""
app/main.py

Main FastAPI application entrypoint for Performance Lab API.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import (
    auth,
    benchmarks,
    dashboard,
    exercises,
    feedback,
    ingest,
    integrations_oura,
    legacy,
    macrocycles,
    objectives,
    planning,
    prescribe,
    shadow,
    simulate,
    weak_points,
    wellness,
)
from app.api.v1.history import router as history_router
from app.api.v1.onboard import router as onboard_router
from app.api.v1.profile import router as profile_router
from app.core.config import DEFAULT_SECRET_KEY, DEV_DEFAULT_ORIGINS, Settings, settings
from app.core.db import engine

logger = logging.getLogger("perflab")

# A 256-bit floor for the HS256 signing key (32 chars). Below this — or the default /
# empty value — a key is too weak to sign auth tokens.
MIN_SECRET_KEY_LENGTH = 32


def _check_production_secrets(cfg: Settings) -> None:
    """Refuse to boot in production with a default, empty, or too-short SECRET_KEY (INT-01).

    ``SECRET_KEY`` signs every JWT (HS256); a default or trivially short key lets anyone
    forge tokens and impersonate any user. Mirror the ``_on_schema_mismatch`` contract:
    fail fast (raise) in production, log a warning elsewhere so local/dev boots aren't
    blocked. Generate a strong key with ``openssl rand -hex 32``.
    """
    key = cfg.SECRET_KEY.strip()
    weak = (not key) or key == DEFAULT_SECRET_KEY or len(key) < MIN_SECRET_KEY_LENGTH
    if not weak:
        return
    message = (
        "SECRET_KEY is unset, the public default, or too short (needs a random value of "
        f"at least {MIN_SECRET_KEY_LENGTH} chars — e.g. `openssl rand -hex 32`). It signs "
        "every auth token; a weak key allows token forgery."
    )
    if cfg.is_production:
        raise RuntimeError(message)
    logger.warning("%s (allowed outside production)", message)


def _check_production_cors(cfg: Settings) -> None:
    """Refuse to boot in production without an explicit prod CORS origin pinned (INT-09).

    Production must pin at least one explicit allowed origin (e.g.
    ``https://perflab.44-198-76-44.nip.io``) rather than relying on the old catch-all
    ``*.railway.app`` regex default. Mirror ``_check_production_secrets``: fail fast
    (raise) in production, log a warning elsewhere so local/dev boots aren't blocked.

    A configured ``ALLOWED_ORIGIN_REGEX`` alone is NOT sufficient — the decision is to
    pin an explicit origin, so we require at least one origin in ``allowed_origins_list``
    that is not one of the local-dev defaults.
    """
    dev_defaults = {o.strip().lower() for o in DEV_DEFAULT_ORIGINS}
    has_explicit = any(
        o.strip().lower() not in dev_defaults for o in cfg.allowed_origins_list
    )
    if has_explicit:
        return
    message = (
        "No explicit production CORS origin is configured; only local-dev origins are "
        "allowed. Pin the prod origin, e.g. "
        "set ALLOWED_ORIGINS=https://perflab.44-198-76-44.nip.io. "
        "A regex alone is not accepted — an explicit origin must be pinned."
    )
    if cfg.is_production:
        raise RuntimeError(message)
    logger.warning("%s (allowed outside production)", message)


def _on_schema_mismatch(message: str) -> None:
    """
    Handle a confirmed schema-vs-migrations mismatch.

    In production we fail fast (raise) so a stale schema can never silently
    serve traffic; elsewhere we log loudly so it surfaces in dev without
    blocking local/test boots.
    """
    logger.error(message)
    if settings.is_production:
        raise RuntimeError(message)


async def _check_alembic_head() -> None:
    """
    Lightweight check that the database is at the expected Alembic head.

    On a *confirmed* mismatch (DB reachable but not at head, or no migrations
    applied) this fails fast in production via ``_on_schema_mismatch``. Errors
    that merely mean we couldn't verify (connectivity, missing table on a fresh
    DB, test setups) stay non-fatal warnings so they don't block first boots.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            current_rev = result.scalar()

        # Compare against the latest revision in the migrations folder
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()
    except Exception as exc:
        # Outside production this is a non-fatal warning (fresh DB, connectivity not
        # ready, test setups). In production, an *inability to verify* the schema must
        # fail closed (INT-13) — the check exists precisely so we never serve traffic
        # against a possibly-stale schema, and swallowing the error defeats it.
        if settings.is_production:
            raise RuntimeError(
                f"Could not verify the database is at the Alembic head in production: {exc}"
            ) from exc
        logger.warning("Could not verify Alembic head (may be first run): %s", exc)
        return

    if not current_rev:
        _on_schema_mismatch(
            "No Alembic version applied to the database. "
            "Run `alembic upgrade head` before starting the app."
        )
    elif current_rev != head_rev:
        _on_schema_mismatch(
            "Database is not at Alembic head! "
            f"Current: {current_rev}, Expected head: {head_rev}. "
            "Run `alembic upgrade head` before starting the app."
        )
    else:
        logger.info("✅ Database at expected Alembic head (%s)", current_rev)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    IMPORTANT: Schema management is handled exclusively by Alembic.
    Do NOT call Base.metadata.create_all here.

    Run `alembic upgrade head` (or your deployment migration step)
    before starting the application against a real database.
    """
    logger.info("🚀 Starting Performance Lab API (app.main:app v0.3.0)...")

    # Optional: lightweight connectivity check (does not mutate schema)
    try:
        async with engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        logger.info("✅ Database connectivity verified")
    except Exception as exc:
        logger.warning("Database connectivity check failed (this may be expected in some test setups): %s", exc)

    # Critical safety check: production must not boot with a forgeable signing key
    _check_production_secrets(settings)

    # Critical safety check: production must pin an explicit CORS origin (no railway default)
    _check_production_cors(settings)

    # Critical safety check: ensure we're not running against a stale schema
    await _check_alembic_head()

    yield

    logger.info("🛑 Shutting down Performance Lab API...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.include_router(onboard_router)
app.include_router(profile_router)
app.include_router(history_router)


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_origin_regex=settings.allowed_origin_regex,  # disabled by default; pin explicit origins
    allow_credentials=True,           # Required for JWT Bearer tokens
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Routers
# ----------------------------------------------------------------------

# Auth (no /v1 prefix — required for OAuth2 compatibility)
app.include_router(auth.router)

# Legacy v0.1 routes (used by frontend HeroFlowColumn)
app.include_router(legacy.router)

# Modern v1 API
app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(prescribe.router, prefix=settings.API_V1_STR)
app.include_router(benchmarks.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(planning.router, prefix=settings.API_V1_STR)
app.include_router(wellness.router, prefix=settings.API_V1_STR)
app.include_router(objectives.router, prefix=settings.API_V1_STR)
app.include_router(macrocycles.router, prefix=settings.API_V1_STR)
app.include_router(feedback.router, prefix=settings.API_V1_STR)
app.include_router(simulate.router, prefix=settings.API_V1_STR)
app.include_router(shadow.router, prefix=settings.API_V1_STR)
app.include_router(integrations_oura.router, prefix=settings.API_V1_STR)

# Future routers (uncomment when ready)
# app.include_router(blocks.router, prefix=settings.API_V1_STR)
app.include_router(weak_points.router, prefix=settings.API_V1_STR)
app.include_router(exercises.router, prefix=settings.API_V1_STR)
# app.include_router(onboarding.router, prefix=settings.API_V1_STR)


# ----------------------------------------------------------------------
# Health check
# ----------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the React SPA shell at the root."""
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/ping", tags=["Health"], response_model=None)
async def ping() -> dict[str, str]:
    """Simple health check endpoint."""
    return {
        "status": "ok",
        "system": "running",
        "version": "0.3.0",
        "project": settings.PROJECT_NAME,
    }


# ----------------------------------------------------------------------
# Global exception handler (optional but recommended)
# ----------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log any unhandled exception (with traceback) and return clean JSON.

    The client gets a generic message (no internal detail leaked); the server
    keeps the full stack trace so 500s are diagnosable instead of vanishing.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )


# Optional: Add security headers middleware in the future

# ── Frontend static files ──────────────────────────────────────────────────────
# Vite outputs: index.html + assets/ (hashed JS/CSS chunks).
# /assets is served by StaticFiles (GET/HEAD only, no routing conflicts).
# SPA fallback (index.html for unknown GET paths) is handled via the 404
# exception handler below — no catch-all route that could shadow API endpoints.
_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
_assets_dir = os.path.join(_static_dir, "assets")

if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static-root")


@app.exception_handler(StarletteHTTPException)
async def spa_404_handler(request: Request, exc: StarletteHTTPException) -> FileResponse | JSONResponse:
    """For GET 404s serve the SPA shell; all other errors return JSON."""
    if exc.status_code == 404 and request.method == "GET":
        index = os.path.join(_static_dir, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
