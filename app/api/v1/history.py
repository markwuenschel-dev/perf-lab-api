"""
app/api/v1/history.py

Read-only history over data the app already persists: the athlete's state
vectors over time (athlete_states) and their logged workouts (workout_logs).
These back the previously-mocked trend/time-travel views (Twin time-travel,
History readiness trend & weekly load, recent sessions) with real data.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.engine.state_bridge import unified_from_athlete_row
from app.models.athlete_state import AthleteState
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.schemas.history import WorkoutLogSummary
from app.schemas.state import UnifiedStateVector

router = APIRouter(prefix="/v1", tags=["history"])


@router.get("/state-history", response_model=list[UnifiedStateVector])
async def state_history(
    limit: int = Query(60, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UnifiedStateVector]:
    """The athlete's recent state vectors, oldest→newest (chart order)."""
    rows = (
        await db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == current_user.id)
            .order_by(AthleteState.timestamp.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [unified_from_athlete_row(r) for r in reversed(rows)]


@router.get("/workouts", response_model=list[WorkoutLogSummary])
async def list_workouts(
    limit: int = Query(50, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WorkoutLog]:
    """The athlete's logged workouts, most recent first."""
    rows = (
        await db.execute(
            select(WorkoutLog)
            .where(WorkoutLog.user_id == current_user.id)
            .order_by(WorkoutLog.logged_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)
