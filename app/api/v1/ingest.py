from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.dose_engine import calculate_stress_dose
from app.models.user import User
from app.schemas.state import LogWorkoutResponse
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import state_service

router = APIRouter(tags=["Ingest"])


@router.post("/simulate-dose", response_model=StressDose)
async def simulate_dose(log: WorkoutLog) -> StressDose:
    return calculate_stress_dose(log)


@router.post("/log-workout", response_model=LogWorkoutResponse)
async def log_workout(
    log: WorkoutLog,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LogWorkoutResponse:
    # P3a: receipt time, captured before any wait for the athlete-state lock.
    received_at = datetime.now(UTC)
    # An event time in the future cannot have happened yet. Accepting it would make it the
    # head and push every later log before it (record-only). A live submission sends
    # timestamp_mode="server_now" instead, so a fast device clock is no obstacle.
    if log.timestamp_mode == "event_time":
        event = log.timestamp if log.timestamp.tzinfo else log.timestamp.replace(tzinfo=UTC)
        if event > received_at:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The workout's timestamp is in the future. Send the time it happened, "
                    "or timestamp_mode=server_now for a workout logged as it ends."
                ),
            )
    # No blanket try/except: an HTTPException keeps its status, and unexpected
    # errors are logged + returned as a clean 500 by the global handler (app.main)
    # instead of being mislabelled a 400 with the internal message leaked.
    return await state_service.process_new_workout(
        db, user_id=current_user.id, log=log, received_at=received_at
    )
