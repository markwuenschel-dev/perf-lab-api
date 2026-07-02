"""Prescription orchestration service — reusable by HTTP routes and cron jobs."""
from __future__ import annotations

from datetime import date
from typing import Any, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescriber import recommend_next_session
from app.logic.workout_history import recent_workout_summaries
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.models.user import AthleteProfile
from app.models.weak_point import WeakPoint
from app.repositories.athlete_context_repository import AthleteContextRepository
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
    *,
    planned_session: PlannedSession | None = None,
) -> WorkoutPrescription:
    """
    Full prescription pipeline for one athlete.
    Auto-initializes state if none exists.
    Callable by HTTP routes, cron jobs, or batch processes.

    When ``planned_session`` is supplied (e.g. the planning route passes the
    session it will display), the prescription is persisted into exactly that
    slot and block context is taken from its owning block — so the displayed
    session is always the persisted one. Otherwise the target is today's pending
    session in the latest active block.
    """
    # Auto-create baseline AthleteState if none exists yet
    repo = AthleteContextRepository(db)
    last_record = await repo.get_latest_state(user_id)

    if not last_record:
        await initialize_athlete_state(db, user_id)
        # re-fetch the newly created state
        last_record = await repo.get_latest_state(user_id)

    state = unified_from_athlete_row(last_record)

    # Fetch active (unresolved) weak-point tags for context injection
    wp_result = await db.execute(
        select(WeakPoint.tag).where(
            WeakPoint.user_id == user_id,
            WeakPoint.resolved_at.is_(None),
        )
    )
    active_weak_points = [row[0] for row in wp_result.all()]

    # Resolve the owning block + the session to prescribe into.
    if planned_session is not None:
        # Caller-supplied target: persist here, take context from its block.
        target_session: PlannedSession | None = planned_session
        block_result = await db.execute(
            select(MesocycleBlock).where(MesocycleBlock.id == planned_session.block_id)
        )
        active_block = block_result.scalars().first()
    else:
        # Resolve today's pending session in the latest active block.
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

        target_session = None
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
            target_session = ps_result.scalars().first()

    block_context: BlockContext | None = None
    if active_block and target_session:
        block_context = BlockContext(
            block_goal=active_block.goal.value,
            session_category=target_session.category,
            is_deload=target_session.is_deload,
            is_benchmark=target_session.is_benchmark,
            week_number=target_session.week_number,
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
        block_context=cast(dict[str, Any] | None, block_context),
    )
    # Persist prescription back to the planned session slot
    if target_session is not None:
        target_session.prescribed_content = rx.to_prescribed_content()
        await db.commit()
    return rx
