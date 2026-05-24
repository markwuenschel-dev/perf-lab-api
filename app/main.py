"""
app/main.py

Main FastAPI application entrypoint for Performance Lab API.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.db import engine
from app.models import Base

from app.api.v1 import auth, benchmarks, dashboard, ingest, legacy, planning, prescribe
from app.api.v1.onboard import router as onboard_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    print("🚀 Starting Performance Lab API...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ All database tables ensured on startup")

    yield  # App runs here

    # Shutdown (optional cleanup)
    print("🛑 Shutting down Performance Lab API...")


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
# app.include_router(weak_points.router, prefix=settings.API_V1_STR)
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
