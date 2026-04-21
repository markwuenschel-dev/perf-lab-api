from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from datetime import date

from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.user import AthleteProfile, User
from app.models.athlete_state import AthleteState
from app.models.weak_point import WeakPoint
from app.models.mesocycle import MesocycleBlock, BlockStatus, PlannedSession, SessionStatus
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

    # Fetch active (unresolved) weak-point tags for context injection
    wp_result = await db.execute(
        select(WeakPoint.tag).where(
            WeakPoint.user_id == effective_user_id,
            WeakPoint.resolved_at.is_(None),
        )
    )
    active_weak_points = [row[0] for row in wp_result.all()]

    # Fetch active block + today's planned session for block-context bias
    block_result = await db.execute(
        select(MesocycleBlock)
        .where(
            MesocycleBlock.user_id == effective_user_id,
            MesocycleBlock.status == BlockStatus.ACTIVE,
        )
        .order_by(MesocycleBlock.created_at.desc())
        .limit(1)
    )
    active_block = block_result.scalars().first()

    planned_session = None
    if active_block:
        ps_result = await db.execute(
            select(PlannedSession)
            .where(
                PlannedSession.block_id == active_block.id,
                PlannedSession.scheduled_date == date.today(),
                PlannedSession.status == SessionStatus.PENDING,
            )
            .limit(1)
        )
        planned_session = ps_result.scalars().first()

    block_context = None
    if active_block and planned_session:
        block_context = {
            "block_goal": active_block.goal.value,
            "session_category": planned_session.category,
            "is_deload": planned_session.is_deload,
            "is_benchmark": planned_session.is_benchmark,
            "week_number": planned_session.week_number,
        }

    profile_result = await db.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == effective_user_id).limit(1)
    )
    profile = profile_result.scalars().first()

    try:
        recent = await recent_workout_summaries(db, effective_user_id)
        kpi_summary = await dashboard_service.latest_kpi_values(db, effective_user_id)
        rx = recommend_next_session(
            state,
            goal=goal,
            recent_sessions=recent,
            kpi_summary=kpi_summary or None,
            active_weak_points=active_weak_points or None,
            available_equipment=(profile.equipment if profile else None),
            block_context=block_context,
        )
        # Persist prescription back to the planned session slot
        if planned_session is not None:
            planned_session.prescribed_content = {
                "type": rx.type,
                "focus": rx.focus,
                "rationale": rx.rationale,
                "duration_min": rx.duration_min,
                "model_version": rx.model_version,
                "exercises": [e.model_dump() for e in rx.exercises],
                "why": rx.why.model_dump() if rx.why else None,
            }
            await db.commit()
        return rx
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate prescription: {str(e)}")
