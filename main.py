import warnings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

warnings.warn(
    "The root 'main.py' (uvicorn main:app) is DEPRECATED. "
    "Use the primary entrypoint: uvicorn app.main:app instead. "
    "This file will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

from app.api.v1 import auth, benchmarks, dashboard, ingest, legacy, prescribe  # noqa: E402

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
)

# CORS for web frontend (tighten origins later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- Register routers --------
app.include_router(auth.router)
app.include_router(ingest.router, prefix="/v1")
app.include_router(prescribe.router, prefix="/v1")
app.include_router(dashboard.router)
app.include_router(benchmarks.router)
app.include_router(legacy.router)


# -------- Endpoints --------

@app.get("/ping")
async def ping():
    return {"status": "ok", "system": "running (DEPRECATED entrypoint — use app.main:app)"}

# NOTE: Schema management is handled exclusively via Alembic.
# The old @app.on_event create_all block has been removed.
# Run `alembic upgrade head` before starting this (deprecated) entrypoint.