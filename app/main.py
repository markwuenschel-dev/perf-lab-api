from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import auth, benchmarks, dashboard, ingest, legacy, prescribe

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://performancelab.netlify.app"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth (no v1 prefix — standard /auth/token for OAuth2 compatibility)
app.include_router(auth.router)

# Legacy v0.1 routes (compute-metrics, program/run, program/strength)
app.include_router(legacy.router)

# Existing v1 routers
app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(prescribe.router, prefix=settings.API_V1_STR)
app.include_router(benchmarks.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix=settings.API_V1_STR)

# TODO: add when implemented
# app.include_router(blocks.router, prefix=settings.API_V1_STR)
# app.include_router(weak_points.router, prefix=settings.API_V1_STR)
# app.include_router(onboarding.router, prefix=settings.API_V1_STR)


@app.get("/ping")
async def ping():
    return {"status": "ok", "system": "running"}
