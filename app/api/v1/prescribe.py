from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.athlete_state import AthleteState
from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescriber import recommend_next_session, WorkoutPrescription

router = APIRouter(tags=["Prescription"])


@router.get("/next-session", response_model=WorkoutPrescription)
async def get_next_session(
    goal: Literal["Strength", "Hypertrophy", "Power", "General"] = Query(
        "Strength",
        description="Optimization goal",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkoutPrescription:
    result = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == current_user.id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        return WorkoutPrescription(
            type="Assessment",
            focus="Establish Baseline",
            rationale="No state history found. Complete onboarding and baseline testing first.",
            duration_min=60,
        )

    state = unified_from_athlete_row(last_record)
    try:
        return recommend_next_session(state, goal=goal)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate prescription: {str(e)}")
