from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.athlete_state import AthleteState
from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescription_finalize import finalize_prescription
from app.logic.prescriber import recommend_next_session
from app.logic.workout_history import recent_workout_summaries
from app.services import dashboard_service
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal

router = APIRouter(tags=["Prescription"])

@router.get("/next-session", response_model=WorkoutPrescription)
async def get_next_session(
    goal: TrainingGoal = Query(TRAINING_GOAL_DEFAULT, description=...),
    user_id: int | None = Query(None, description="DEV ONLY — remove in production"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),   # ← put this back
) -> WorkoutPrescription:
    effective_user_id = user_id or current_user.id
    """DEV-friendly version that auto-initializes baseline state."""
    effective_user_id = user_id or 1

    # Auto-create baseline AthleteState if none exists yet
    result = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == effective_user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        from app.services.state_service import initialize_athlete_state
        await initialize_athlete_state(db, effective_user_id)
        # re-fetch the newly created state
        result = await db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == effective_user_id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )
        last_record = result.scalars().first()

    state = unified_from_athlete_row(last_record)

    try:
        recent = await recent_workout_summaries(db, effective_user_id)
        kpi_summary = await dashboard_service.latest_kpi_values(db, effective_user_id)
        return recommend_next_session(
            state,
            goal=goal,
            recent_sessions=recent,
            kpi_summary=kpi_summary or None,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate prescription: {str(e)}")