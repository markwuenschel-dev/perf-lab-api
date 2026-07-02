"""Prescription orchestration service — reusable by HTTP routes and cron jobs."""
from __future__ import annotations

from datetime import date
from typing import Any, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.prescriber import recommend_next_session
from app.logic.workout_history import recent_workout_summaries
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.models.user import AthleteProfile
from app.models.weak_point import WeakPoint
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.services import dashboard_service
from app.services.decision_telemetry import persist_prescription_decision
from app.services.objective_service import active_objective_signals
from app.services.planning_service import count_block_skips
from app.services.state_service import load_or_init_current_state


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
    target_session_minutes: int | None
    accessory_emphasis: str | None
    accessory_focus: list[str] | None
    # Objective taper + domain-emphasis (Phase 4a). Populated regardless of
    # whether an active block/planned session exists — see prescribe_for_athlete.
    objective_taper: bool
    objective_domain: str | None


def resolve_effective_goal(
    *,
    block_goal: str | None,
    query_goal: TrainingGoal | None,
    profile_goal: str | None,
) -> str:
    """Resolve the training-goal string to drive a prescription (ADR-0030/0038).

    Precedence: active block goal > explicit `goal` query param > the
    athlete's stored `profile.primary_goal` > TRAINING_GOAL_DEFAULT. Block
    goals aren't 1:1 with TrainingGoal; the prescriber resolves any goal
    string to a canonical domain, so returning `str` here is safe — callers
    cast to `TrainingGoal` for the downstream call.
    """
    if block_goal is not None:
        return block_goal
    return query_goal or profile_goal or TRAINING_GOAL_DEFAULT


async def prescribe_for_athlete(
    db: AsyncSession,
    user_id: int,
    goal: TrainingGoal | None,
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
    state = await load_or_init_current_state(db, user_id)

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

    block_context: BlockContext = BlockContext()
    if active_block and target_session:
        block_context.update(
            block_goal=active_block.goal.value,
            session_category=target_session.category,
            is_deload=target_session.is_deload,
            is_benchmark=target_session.is_benchmark,
            week_number=target_session.week_number,
            duration_weeks=active_block.duration_weeks,
            deload_every_n_weeks=active_block.deload_every_n_weeks,
            deload_volume_factor=active_block.deload_volume_factor,
            recent_skips=await count_block_skips(db, user_id, active_block.id),
            target_session_minutes=active_block.target_session_minutes,
            accessory_emphasis=active_block.accessory_emphasis,
            accessory_focus=active_block.accessory_focus,
        )

    # Objective taper + domain-emphasis (Phase 4a) apply regardless of
    # whether an active block/planned session exists.
    objective_signals = await active_objective_signals(db, user_id)
    block_context["objective_taper"] = objective_signals["taper"]
    block_context["objective_domain"] = objective_signals["domain"]

    profile_result = await db.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == user_id).limit(1)
    )
    profile = profile_result.scalars().first()

    # ADR-0030: when a block is active, the day's training intent comes from the block
    # (resolved to a canonical domain by the prescriber, ADR-0038). With no active
    # block, resolution order is: explicit query `goal` > the athlete's stored
    # `profile.primary_goal` > the hardcoded TRAINING_GOAL_DEFAULT.
    effective_goal = resolve_effective_goal(
        block_goal=active_block.goal.value if active_block is not None else None,
        query_goal=goal,
        profile_goal=profile.primary_goal if profile is not None else None,
    )

    recent = await recent_workout_summaries(db, user_id)
    kpi_summary = await dashboard_service.latest_kpi_values(db, user_id)
    # candidate_log_out captures the full ranked pool for decision telemetry
    # (Workstream B). Passing an empty list is a no-op for selection — the
    # prescriber only fills it, never reads it (see recommend_next_session).
    candidate_log: list[SessionCandidate] = []
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
        candidate_log_out=candidate_log,
    )
    # Persist prescription back to the planned session slot
    if target_session is not None:
        target_session.prescribed_content = rx.to_prescribed_content()
        await db.commit()

    # Best-effort decision telemetry — written after the prescription is
    # finalized/committed so a telemetry failure can never alter or block `rx`.
    await persist_prescription_decision(
        db,
        user_id,
        rx,
        candidate_log,
        goal=str(effective_goal),
        decision_mode="adaptive",
        planned_session_id=target_session.id if target_session is not None else None,
        state_snapshot=state.model_dump(mode="json"),
        block_context=cast(dict[str, Any], dict(block_context)),
    )
    return rx
