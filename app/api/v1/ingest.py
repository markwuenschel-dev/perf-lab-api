from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.models.user import User
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import state_service

router = APIRouter(tags=["Ingest"])


@router.post("/simulate-dose", response_model=StressDose)
async def simulate_dose(log: WorkoutLog) -> StressDose:
    return calculate_stress_dose(log)


@router.post("/log-workout", response_model=UnifiedStateVector)
async def log_workout(
    log: WorkoutLog,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UnifiedStateVector:
    try:
        return await state_service.process_new_workout(
            db, user_id=current_user.id, log=log
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update state: {str(e)}") from e
