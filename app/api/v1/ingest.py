from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.schemas.workouts import WorkoutLog, StressDose
from app.schemas.state import UnifiedStateVector
from app.logic.dose_engine import calculate_stress_dose
from app.services import state_service


router = APIRouter(tags=["Ingest"])


@router.post("/simulate-dose", response_model=StressDose)
async def simulate_dose(log: WorkoutLog) -> StressDose:

    return calculate_stress_dose(log)


@router.post("/log-workout", response_model=UnifiedStateVector)
async def log_workout(
    log: WorkoutLog,
    db: AsyncSession = Depends(get_db),
) -> UnifiedStateVector:

    user_id = 1  # TODO: replace with real user from auth

    try:
        return await state_service.process_new_workout(db, user_id=user_id, log=log)
    except Exception as e:
        # In production: log the stack trace here
        raise HTTPException(
            status_code=400,
            detail=f"Failed to update state: {str(e)}",
        )
