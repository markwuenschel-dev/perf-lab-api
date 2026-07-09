"""Prescription orchestration service — reusable by HTTP routes and cron jobs."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.logic import e1rm as e1rm_logic
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.planning import periodization_envelope
from app.logic.prescriber import recommend_next_session
from app.logic.workout_history import recent_workout_summaries
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.models.weak_point import WeakPoint
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.schemas.wellness import ReadinessScore
from app.services import dashboard_service, readiness_service
from app.services.decision_telemetry import persist_prescription_decision
from app.services.mpc_shadow_service import record_mpc_shadow
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


def _readiness_audit(
    readiness: ReadinessScore, readiness_override: float | None
) -> dict[str, Any]:
    """Shadow-history audit of how readiness influenced the plan (ADR-0052).

    Records that the *score* nudged the plan and that *confidence* did NOT gate it
    (``enforced``/``confidence_used_by_prescriber`` are always false in P8), so P13 can
    later answer "what would confidence-gating have changed?" from real history.
    """
    conf = getattr(readiness, "confidence", None)
    gate = conf.recommendation_gate if conf is not None else None
    score = readiness.score
    modeled = readiness.modeled
    adjustment = (
        round((score - modeled) / 100.0, 4)
        if score is not None and modeled is not None
        else None
    )
    return {
        "readiness_score_used_by_prescriber": readiness_override is not None,
        "readiness_score": score,
        "modeled": modeled,
        "readiness_score_adjustment": adjustment,
        "confidence_score": conf.score if conf is not None else None,
        "confidence_band": conf.band if conf is not None else None,
        "recommendation_gate": (
            {
                "max_recommendation_authority": gate.max_recommendation_authority,
                "enforced": gate.enforced,
            }
            if gate is not None
            else None
        ),
        "confidence_used_by_prescriber": False,
        "signal_summary": (
            conf.signal_summary.model_dump() if conf is not None else None
        ),
    }


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


def _first_int(value: str | None) -> int | None:
    """First integer in a reps string like '5' or '4-6' or '8-12/side'."""
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def _envelope_rpe_cap(block_context: BlockContext) -> float:
    """RPE cap for working sets from the ADR-0029 envelope, else a neutral 8.0."""
    wk = block_context.get("week_number")
    dur = block_context.get("duration_weeks")
    if wk and dur:
        env = periodization_envelope(
            int(dur), int(wk), int(block_context.get("deload_every_n_weeks") or 4)
        )
        return env.rpe_high
    return 8.0


async def _e1rm_codes_for_names(db: AsyncSession, names: list[str]) -> dict[str, str]:
    """Catalog lookup: exercise name → its ``e1rm_benchmark_code`` (only lifts that have one)."""
    if not names:
        return {}
    res = await db.execute(
        select(Exercise.name, Exercise.e1rm_benchmark_code).where(
            Exercise.name.in_(names),
            Exercise.e1rm_benchmark_code.isnot(None),
        )
    )
    return {name: code for name, code in res.all() if code}


async def _current_e1rm_values(
    db: AsyncSession, user_id: int, codes: set[str]
) -> dict[str, float]:
    """Latest valid e1RM raw_value per benchmark code for this athlete."""
    if not codes:
        return {}
    res = await db.execute(
        select(
            BenchmarkDefinition.code,
            BenchmarkObservation.raw_value,
        )
        .join(
            BenchmarkObservation,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code.in_(codes),
            BenchmarkObservation.validity_status == "valid",
        )
        .order_by(BenchmarkObservation.observed_at.desc())
    )
    latest: dict[str, float] = {}
    for code, raw in res.all():
        if code not in latest and raw is not None:
            latest[code] = float(raw)
    return latest


async def _enrich_exercises_with_load(
    db: AsyncSession,
    user_id: int,
    rx: WorkoutPrescription,
    block_context: BlockContext,
) -> None:
    """ADR-0045: resolve %e1RM → suggested kg for prescribed lifts with a current e1RM.

    Mutates ``rx.exercises`` in place. Lifts without a mapped e1RM benchmark or without
    a logged e1RM keep RPE-only autoregulation (the existing ``load_note``).
    """
    if not rx.exercises:
        return
    code_by_name = await _e1rm_codes_for_names(db, [ex.name for ex in rx.exercises])
    if not code_by_name:
        return
    e1rm_by_code = await _current_e1rm_values(db, user_id, set(code_by_name.values()))
    if not e1rm_by_code:
        return

    rpe_cap = _envelope_rpe_cap(block_context)
    for ex in rx.exercises:
        code = code_by_name.get(ex.name)
        e1rm = e1rm_by_code.get(code) if code else None
        if e1rm is None:
            continue
        reps = _first_int(ex.reps) or 5
        pct = e1rm_logic.percent_1rm(reps, rpe_cap)
        load = e1rm_logic.suggested_load_kg(e1rm, reps, rpe_cap)
        ex.percent_e1rm = round(pct, 3)
        ex.prescribed_load_kg = load
        ex.rpe_cap = rpe_cap
        ex.e1rm_basis_kg = round(e1rm, 1)
        ex.load_note = f"~{load:g} kg · {round(pct * 100)}% e1RM · cap RPE {rpe_cap:g}"


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

    profile = await AthleteProfileRepository(db).get_for_user(user_id)

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

    # ADR-0052: acute wellness may transparently nudge the plan through the readiness
    # *score* channel (bounded by WELLNESS_WEIGHT). Confidence is computed here too but is
    # REPORT-ONLY — it is logged for P13 shadow history and never gates the prescription.
    readiness = await readiness_service.compute_readiness(db, user_id)
    readiness_override = None if readiness.score is None else readiness.score / 100.0
    readiness_audit = _readiness_audit(readiness, readiness_override)

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
        readiness_override=readiness_override,
    )
    # ADR-0045: strength prescriptions speak in load — resolve %e1RM → suggested kg
    # (+ RPE cap) for lifts the athlete has a current e1RM for, before persisting.
    await _enrich_exercises_with_load(db, user_id, rx, block_context)

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
        block_context={**dict(block_context), "readiness_audit": readiness_audit},
    )

    # Best-effort shadow MPC (ADR-0042): re-rank the same candidate pool by
    # receding-horizon lookahead and log MPC-vs-greedy. Capture-only — never alters `rx`.
    await record_mpc_shadow(db, user_id, state, candidate_log, str(effective_goal))

    return rx
