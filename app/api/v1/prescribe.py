from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.db import get_db
from app.models.athlete_state import AthleteState
from app.schemas.state import UnifiedStateVector
from app.logic.prescriber import recommend_next_session, WorkoutPrescription


router = APIRouter(tags=["Prescription"])


@router.get("/next-session", response_model=WorkoutPrescription)
async def get_next_session(
    goal: Literal["Strength", "Hypertrophy", "Power", "General"] = Query(
        "Strength",
        description="Optimization goal",
    ),
    db: AsyncSession = Depends(get_db),
) -> WorkoutPrescription:
    """
    The Oracle Endpoint.
    Reads S(t) and returns the next best action u(t) as a WorkoutPrescription.
    """
    user_id = 1  # TODO: replace with real user from auth

    result = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        return WorkoutPrescription(
            type="Assessment",
            focus="Establish Baseline",
            rationale="No state history available. Recommend baseline testing / assessment.",
            duration_min=60,
        )

    state = UnifiedStateVector.model_validate(last_record)
    try:
        return recommend_next_session(state, goal=goal)
    except Exception as e:
        # Defensive: if goal logic blows up, fail gracefully
        raise HTTPException(
            status_code=400,
            detail=f"Failed to generate prescription: {str(e)}",
        )
