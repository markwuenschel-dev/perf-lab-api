"""Simulation endpoints (Phase 7 — goal-anchored program).

``POST /v1/simulate/projection`` — a goal-aware, non-mutating forward projection
of the athlete's 8 capacity axes over an N-week horizon. The multi-week analog of
``POST /v1/simulate-dose``, but authed because it needs the caller's current state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.projection import ProjectionRequest, ProjectionResponse
from app.services.projection_service import project_trajectory
from app.services.state_service import (
    has_state,
    load_or_init_current_state,
    preview_baseline_state,
)

router = APIRouter(prefix="/simulate", tags=["Simulate"])


@router.post("/projection", response_model=ProjectionResponse)
async def simulate_projection(
    payload: ProjectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectionResponse:
    """Project the athlete's capacity axes forward under a hypothetical plan.

    Loads the caller's current state — or, for an athlete with none, the baseline that
    initialization would seed, built in memory — then runs the pure projection engine.
    No state is written: this is a compute-and-return preview (F1).
    """
    # Never initialize here: an athlete with no state gets the in-memory preview baseline.
    # With a state row present, load_or_init_current_state only loads.
    if await has_state(db, current_user.id):
        state = await load_or_init_current_state(db, current_user.id)
    else:
        state = preview_baseline_state(current_user.id)
    return project_trajectory(state, payload)
