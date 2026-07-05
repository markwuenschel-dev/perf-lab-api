from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TrainingGoal
from app.services.prescription_service import prescribe_for_athlete

router = APIRouter(tags=["Prescription"])


@router.get("/next-session", response_model=WorkoutPrescription)
async def get_next_session(
    goal: TrainingGoal | None = Query(None, description="Training goal to prescribe for; defaults to the athlete's stored primary goal, then Strength."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkoutPrescription:
    """DEV-friendly version that auto-initializes baseline state."""
    # No blanket try/except: an HTTPException raised downstream keeps its status,
    # and any unexpected error is logged + returned as a clean 500 by the global
    # handler (app.main) rather than being mislabelled a 400 with leaked internals.
    return await prescribe_for_athlete(db, current_user.id, goal)
