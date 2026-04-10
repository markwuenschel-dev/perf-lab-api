from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.db import engine, Base
from app.api.v1 import auth, ingest, prescribe, dashboard, benchmarks, legacy

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
    return {"status": "ok", "system": "running"}

@app.on_event("startup")
async def init_tables():
    # Dev-only auto-migrations: ensure models are imported so metadata is populated
    from app.models import athlete_state, user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)