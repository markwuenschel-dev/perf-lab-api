from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import ingest, prescribe
from app.api import auth  # new

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth (no v1 prefix — standard /auth/token for OAuth2 compatibility)
app.include_router(auth.router)

# Existing v1 routers
app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(prescribe.router, prefix=settings.API_V1_STR)

# TODO: add when implemented
# app.include_router(blocks.router, prefix=settings.API_V1_STR)
# app.include_router(weak_points.router, prefix=settings.API_V1_STR)
# app.include_router(onboarding.router, prefix=settings.API_V1_STR)


@app.get("/ping")
async def ping():
    return {"status": "ok", "system": "running"}
