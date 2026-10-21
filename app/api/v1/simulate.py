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
from app.core.errors import CanonicalStateInvalid, normalize_decode_error
from app.engine.engine_state_codec import EngineStateDecodeError
from app.models.user import User
from app.schemas.projection import ProjectionRequest, ProjectionResponse
from app.services.projection_service import project_trajectory
from app.services.state_service import load_current_state_strict, preview_baseline_state

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
    # INT-15 (C1): strict. A projection is shown to the athlete as their own forecast and may
    # steer the plan they choose, so it is not display-only in the sense that permits
    # recovery. A row that exists but cannot be decoded refuses (409, capability
    # ``projection``) rather than projecting a legacy reconstruction or a default baseline.
    try:
        state = await load_current_state_strict(db, current_user.id)
    except EngineStateDecodeError as exc:
        raise CanonicalStateInvalid(
            capability="projection", normalized_reason=normalize_decode_error(exc)
        ) from exc
    if state is None:
        state = preview_baseline_state(current_user.id)
    return project_trajectory(state, payload)
