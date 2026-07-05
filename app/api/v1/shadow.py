"""Read-only inspection of an athlete's shadow subsystems (ADR-0041/0042/0043).

``GET /v1/shadow/summary`` — the latest EKF belief + calibration, MPC-vs-greedy agreement,
recovery personalization, and recovery-clearance shadow for the current athlete. Purely
observational; triggers no computation and changes no state.
"""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.services.shadow_summary_service import athlete_shadow_summary

router = APIRouter()


@router.get("/shadow/summary", tags=["Shadow"])
async def get_shadow_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return await athlete_shadow_summary(db, current_user.id)
