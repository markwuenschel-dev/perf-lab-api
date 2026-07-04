"""Wellness ingestion + the canonical readiness scalar (P5 / PDR-0005).

``POST /v1/wellness``   ingest one acute daily-wellness sample (idempotent per day/source)
``GET  /v1/wellness``   recent samples for the athlete
``GET  /v1/readiness``  the one backend-owned readiness number (combine rule: ADR-0026)
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.wellness import ReadinessScore, WellnessSampleIn, WellnessSampleOut
from app.services import readiness_service, recovery_shadow_service

router = APIRouter()


@router.post("/wellness", response_model=WellnessSampleOut, tags=["Wellness"])
async def ingest_wellness(
    payload: WellnessSampleIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WellnessSampleOut:
    sample = await readiness_service.upsert_wellness_sample(db, current_user.id, payload)
    # Shadow-only (Q2 recovery priors): record baseline-vs-learned clearance multipliers.
    # Best-effort — never affects the response or a live decision.
    await recovery_shadow_service.record_recovery_shadow(db, current_user.id, sample)
    return WellnessSampleOut.model_validate(sample)


@router.get("/wellness", response_model=list[WellnessSampleOut], tags=["Wellness"])
async def list_wellness(
    limit: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WellnessSampleOut]:
    samples = await readiness_service.list_wellness_samples(db, current_user.id, limit=limit)
    return [WellnessSampleOut.model_validate(s) for s in samples]


@router.get("/readiness", response_model=ReadinessScore, tags=["Readiness"])
async def get_readiness(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReadinessScore:
    return await readiness_service.compute_readiness(db, current_user.id)
