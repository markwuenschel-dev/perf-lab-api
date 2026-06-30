from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.services.prescription_service import prescribe_for_athlete

router = APIRouter(tags=["Prescription"])


@router.get("/next-session", response_model=WorkoutPrescription)
async def get_next_session(
    goal: TrainingGoal = Query(TRAINING_GOAL_DEFAULT, description="Training goal to prescribe for; defaults to the athlete's primary goal."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkoutPrescription:
    """DEV-friendly version that auto-initializes baseline state."""
    try:
        return await prescribe_for_athlete(db, current_user.id, goal)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate prescription: {str(e)}") from e
