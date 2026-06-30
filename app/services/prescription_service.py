"""Prescription orchestration service — reusable by HTTP routes and cron jobs."""
from __future__ import annotations

from datetime import date
from typing import TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescriber import recommend_next_session
from app.logic.workout_history import recent_workout_summaries
from app.models.athlete_state import AthleteState
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.models.user import AthleteProfile
from app.models.weak_point import WeakPoint
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TrainingGoal
from app.services import dashboard_service
from app.services.planning_service import count_block_skips
from app.services.state_service import initialize_athlete_state


class BlockContext(TypedDict, total=False):
    block_goal: str
    session_category: str | None
    is_deload: bool
    is_benchmark: bool
    week_number: int | None
    duration_weeks: int
    deload_every_n_weeks: int
    deload_volume_factor: float | None
    recent_skips: int


async def prescribe_for_athlete(
    db: AsyncSession,
    user_id: int,
    goal: TrainingGoal,
) -> WorkoutPrescription:
    """
    Full prescription pipeline for one athlete.
    Auto-initializes state if none exists.
    Callable by HTTP routes, cron jobs, or batch processes.
    """
    # Auto-create baseline AthleteState if none exists yet
    result = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        await initialize_athlete_state(db, user_id)
        # re-fetch the newly created state
        result = await db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )
        last_record = result.scalars().first()

    state = unified_from_athlete_row(last_record)

    # Fetch active (unresolved) weak-point tags for context injection
    wp_result = await db.execute(
        select(WeakPoint.tag).where(
            WeakPoint.user_id == user_id,
            WeakPoint.resolved_at.is_(None),
        )
    )
    active_weak_points = [row[0] for row in wp_result.all()]

    # Fetch active block + today's planned session for block-context bias
    block_result = await db.execute(
        select(MesocycleBlock)
        .where(
            MesocycleBlock.user_id == user_id,
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

    block_context: BlockContext | None = None
    if active_block and planned_session:
        block_context = BlockContext(
            block_goal=active_block.goal.value,
            session_category=planned_session.category,
            is_deload=planned_session.is_deload,
            is_benchmark=planned_session.is_benchmark,
            week_number=planned_session.week_number,
            duration_weeks=active_block.duration_weeks,
            deload_every_n_weeks=active_block.deload_every_n_weeks,
            deload_volume_factor=active_block.deload_volume_factor,
            recent_skips=await count_block_skips(db, user_id, active_block.id),
        )

    profile_result = await db.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == user_id).limit(1)
    )
    profile = profile_result.scalars().first()

    # ADR-0030: when a block is active, the day's training intent comes from the block
    # (resolved to a canonical domain by the prescriber, ADR-0038); the `goal` query
    # param is the fallback for athletes with no active block.
    effective_goal = active_block.goal.value if active_block is not None else goal

    recent = await recent_workout_summaries(db, user_id)
    kpi_summary = await dashboard_service.latest_kpi_values(db, user_id)
    rx = recommend_next_session(
        state,
        # Block goals aren't 1:1 with TrainingGoal; the prescriber resolves any
        # goal string to a canonical domain (ADR-0038), so the cast is safe.
        goal=cast(TrainingGoal, effective_goal),
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
