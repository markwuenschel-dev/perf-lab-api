"""
app/api/v1/history.py

Read-only history over data the app already persists: the athlete's state
vectors over time (athlete_states) and their logged workouts (workout_logs).
These back the previously-mocked trend/time-travel views (Twin time-travel,
History readiness trend & weekly load, recent sessions) with real data.

Both reads go through the repository seam + a state_service loader (AUD-C15) —
the routes no longer own AthleteState query or unified_from_athlete_row
conversion knowledge (see CONTEXT.md).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.history import WorkoutLogSummary
from app.schemas.state import UnifiedStateVector
from app.services import state_service

router = APIRouter(prefix="/v1", tags=["history"])


@router.get("/state-history", response_model=list[UnifiedStateVector])
async def state_history(
    limit: int = Query(60, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UnifiedStateVector]:
    """The athlete's recent state vectors, oldest→newest (chart order)."""
    return await state_service.load_recent_states(db, current_user.id, limit)


@router.get("/workouts", response_model=list[WorkoutLogSummary])
async def list_workouts(
    limit: int = Query(50, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WorkoutLog]:
    """The athlete's logged workouts, most recent first."""
    rows = await AthleteContextRepository(db).list_recent_workouts(current_user.id, limit)
    return list(rows)
