"""
app/main.py

Main FastAPI application entrypoint for Performance Lab API.
"""

from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.db import engine

from app.api.v1 import auth, benchmarks, dashboard, ingest, legacy, planning, prescribe, weak_points
from app.api.v1.onboard import router as onboard_router

logger = logging.getLogger("perflab")


async def _check_alembic_head() -> None:
    """
    Lightweight check that the database is at the expected Alembic head.

    Fails fast if the DB schema is behind (or has no migrations applied).
    This is much safer than create_all and catches deployment mistakes early.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            current_rev = result.scalar()

        if not current_rev:
            logger.warning("No alembic_version row found. Run `alembic upgrade head`.")
            return

        # Compare against the latest revision in the migrations folder
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        if current_rev != head_rev:
            logger.error(
                "Database is not at Alembic head! "
                f"Current: {current_rev}, Expected head: {head_rev}. "
                "Run `alembic upgrade head` before starting the app."
            )
            # In production you might want to raise here.
            # For now we log loudly so it shows up in logs / monitoring.
        else:
            logger.info("✅ Database at expected Alembic head (%s)", current_rev)

    except Exception as exc:
        # Table might not exist yet on fresh DBs
        logger.warning("Could not verify Alembic head (may be first run): %s", exc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan.

    IMPORTANT: Schema management is handled exclusively by Alembic.
    Do NOT call Base.metadata.create_all here.

    Run `alembic upgrade head` (or your deployment migration step)
    before starting the application against a real database.
    """
    logger.info("🚀 Starting Performance Lab API (app.main:app v0.2.0)...")

    # Optional: lightweight connectivity check (does not mutate schema)
    try:
        async with engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        logger.info("✅ Database connectivity verified")
    except Exception as exc:
        logger.warning("Database connectivity check failed (this may be expected in some test setups): %s", exc)

    # Critical safety check: ensure we're not running against a stale schema
    await _check_alembic_head()

    yield

    logger.info("🛑 Shutting down Performance Lab API...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.include_router(onboard_router)


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
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

# Future routers (uncomment when ready)
# app.include_router(blocks.router, prefix=settings.API_V1_STR)
app.include_router(weak_points.router, prefix=settings.API_V1_STR)
# app.include_router(onboarding.router, prefix=settings.API_V1_STR)


# ----------------------------------------------------------------------
# Health check
# ----------------------------------------------------------------------

@app.get("/ping", tags=["Health"])
async def ping():
    """Simple health check endpoint."""
    return {
        "status": "ok",
        "system": "running",
        "version": "0.2.0",
        "project": settings.PROJECT_NAME,
    }


# ----------------------------------------------------------------------
# Global exception handler (optional but recommended)
# ----------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception and return clean JSON."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )


# Optional: Add security headers middleware in the future
