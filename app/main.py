from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import ingest, prescribe

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
)

# CORS (helps when you hook up your front-end later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire up routers
app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(prescribe.router, prefix=settings.API_V1_STR)

@app.get("/ping")
async def ping():
    return {"status": "ok", "system": "running"}
