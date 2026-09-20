"""Prescription orchestration service — reusable by HTTP routes and cron jobs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.errors import CanonicalStateInvalid, normalize_decode_error
from app.engine import feature_flags
from app.engine.engine_state_codec import EngineStateDecodeError
from app.logic import strength_calibration as sc
from app.logic import uncertainty_conservatism
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.exercise_slot import CatalogExercise
from app.logic.planning import periodization_envelope
from app.logic.prescriber import recommend_next_session
from app.logic.prescription_evidence import (
    EXPLAIN_NO_EVIDENCE,
    BasisSelection,
    explain_missing_basis,
)
from app.logic.workout_history import recent_workout_summaries
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession
from app.models.weak_point import WeakPoint
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.repositories.benchmark_observation_repository import select_prescription_basis
from app.schemas.prescription import (
    ConservatismSummary,
    LoadExplanation,
    LoadExplanationReason,
    WorkoutPrescription,
    structure_from_exercises,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.schemas.wellness import ReadinessScore
from app.services import dashboard_service, readiness_service, strength_decline_service
from app.services.decision_telemetry import persist_prescription_decision
from app.services.mpc_shadow_service import record_mpc_shadow
from app.services.objective_service import active_objective_signals
from app.services.planning_service import block_adherence_signals, get_today_session
from app.services.state_service import (
    load_current_state_strict,
    load_or_init_current_state_strict,
)


class BlockContext(TypedDict, total=False):
    block_goal: str
    session_category: str | None
    # The planned slot's OWN canonical domain (a045). None for a session planned before the
    # column existed, or one whose template recorded no domain — the prescriber then falls
    # back to the block goal, exactly as before.
    session_domain: str | None
    # The block's workload preference: easy | medium | hard (None = medium).
    intensity: str | None
    is_deload: bool
    is_benchmark: bool
    week_number: int | None
    duration_weeks: int
    deload_every_n_weeks: int
    deload_volume_factor: float | None
    # Adherence evidence, both from one block-scoped query so they cannot
    # double-count a session (ADR-0070 — status owns occurrence, feedback
    # contributes only the modification dimension).
    recent_skips: int
    recent_modifications: int
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
    """RPE cap for working sets from the ADR-0029 envelope, else a neutral 8.0.

    The block's workload preference shifts the envelope's band (and only on working weeks —
    deload and taper ignore it), which is what makes "hard" reach the prescribed load rather
    than stopping at the session's shape. Uncertainty conservatism may still lower the result.
    """
    wk = block_context.get("week_number")
    dur = block_context.get("duration_weeks")
    if wk and dur:
        env = periodization_envelope(
            int(dur),
            int(wk),
            int(block_context.get("deload_every_n_weeks") or 4),
            intensity=block_context.get("intensity"),
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


async def _weak_point_tags_for_names(
    db: AsyncSession, names: list[str]
) -> dict[str, list[str]]:
    """Catalog lookup: exercise name -> the weak-point tags that exercise addresses."""
    if not names:
        return {}
    res = await db.execute(
        select(Exercise.name, Exercise.weak_point_tags).where(Exercise.name.in_(names))
    )
    return {name: list(tags or []) for name, tags in res.all()}


async def _enrich_exercises_with_weak_point_tags(
    db: AsyncSession, rx: WorkoutPrescription, active_weak_points: list[str]
) -> None:
    """Tell the athlete which of THEIR flagged deficits each exercise addresses.

    ``ExercisePrescription.weak_point_tags`` advertised exactly this and was never assigned
    at any of the three construction sites, so it was always ``[]`` - the field claimed a
    linkage the response never carried, which is why "which goals are being advanced" was
    the weakest of the eight explanations.

    Populated with the INTERSECTION of the exercise's catalog tags and the athlete's active
    (unresolved) weak points, not the raw catalog tags. The catalog list says what an
    exercise can address in general; the intersection says what it addresses for this
    athlete, which is the question the explanation is actually asking. An empty list then
    means "addresses none of your active weak points" - real information, not an absence.

    Mutates ``rx.exercises`` in place. Read-only against the database.
    """
    if not rx.exercises or not active_weak_points:
        return
    tags_by_name = await _weak_point_tags_for_names(db, [ex.name for ex in rx.exercises])
    active = set(active_weak_points)
    for ex in rx.exercises:
        ex.weak_point_tags = sorted(active.intersection(tags_by_name.get(ex.name, [])))


async def _current_e1rm_values(
    db: AsyncSession, user_id: int, codes: set[str], *, as_of: datetime
) -> tuple[dict[str, float], dict[str, BasisSelection]]:
    """The e1RM that may size a load at ``as_of``, per benchmark code (S2), and the
    selection behind every requested code.

    Selected by ``select_prescription_basis`` — the selection
    ``state_service.prelog_e1rm_denominators`` also uses — so for the same evidence and the
    same ``as_of`` the prescribed load and the dose-intensity denominator resolve the same
    e1RM (ADR-0056). Codes with no qualifying evidence are absent from the values; their
    selections still say why, which is what the athlete is told beside the exercise (N1).
    """
    if not codes:
        return {}, {}
    selections = await select_prescription_basis(db, user_id, codes, as_of=as_of)
    values = {
        code: float(selection.selected.raw_value)
        for code, selection in selections.items()
        if selection.selected is not None and selection.selected.raw_value is not None
    }
    return values, selections


async def _load_types_for_names(db: AsyncSession, names: list[str]) -> dict[str, str]:
    """Catalog lookup: exercise name → its ``load_type``."""
    if not names:
        return {}
    res = await db.execute(
        select(Exercise.name, Exercise.load_type).where(Exercise.name.in_(names))
    )
    return dict(res.tuples().all())


def _utc_aware(moment: datetime) -> datetime:
    """Stored timestamps are naive UTC; the published contract carries aware UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _explanation_for(
    code: str, selection: BasisSelection | None, evaluated_at: datetime
) -> LoadExplanation:
    """The explanation for a lift that has an e1RM benchmark."""
    if selection is not None and selection.selected is not None:
        performed = selection.selected.performed_at
        return LoadExplanation(
            status="recommended",
            benchmark_code=code,
            evaluated_at=evaluated_at,
            evidence_performed_at=_utc_aware(performed) if performed is not None else None,
        )
    explanatory = selection.explanatory if selection is not None else None
    reason = explain_missing_basis(selection) if selection is not None else EXPLAIN_NO_EVIDENCE
    return LoadExplanation(
        status="no_qualifying_evidence",
        reason=cast(LoadExplanationReason, reason),
        benchmark_code=code,
        evaluated_at=evaluated_at,
        evidence_performed_at=(
            _utc_aware(explanatory.performed_at)
            if explanatory is not None and explanatory.performed_at is not None
            else None
        ),
    )


async def _explain_loads(
    db: AsyncSession,
    rx: WorkoutPrescription,
    code_by_name: dict[str, str],
    selections: dict[str, BasisSelection],
    *,
    evaluated_at: datetime,
) -> None:
    """N1: say, beside every prescribed exercise, whether it carries a suggested weight.

    A lift with an e1RM benchmark is ``recommended`` or ``no_qualifying_evidence`` (with the
    selector's category). An externally loaded exercise with no benchmark is
    ``not_supported`` — no athlete report could size it, which is a different thing to tell
    the athlete than "your evidence does not qualify". Everything else (bodyweight, time,
    distance, or not in the catalog) has nothing to explain and stays ``None``.

    Mutates ``rx.exercises`` in place. Read-only against the database.
    """
    load_types = await _load_types_for_names(
        db, [ex.name for ex in rx.exercises if ex.name not in code_by_name]
    )
    at = _utc_aware(evaluated_at)
    for ex in rx.exercises:
        code = code_by_name.get(ex.name)
        if code is not None:
            ex.load_explanation = _explanation_for(code, selections.get(code), at)
        elif sc.is_loaded(load_types.get(ex.name)):
            ex.load_explanation = LoadExplanation(status="not_supported", evaluated_at=at)
        else:
            ex.load_explanation = None


async def _standardization_rules_for_codes(
    db: AsyncSession, codes: set[str]
) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    res = await db.execute(
        select(BenchmarkDefinition.code, BenchmarkDefinition.standardization_rules).where(
            BenchmarkDefinition.code.in_(codes)
        )
    )
    return {code: (rules or {}) for code, rules in res.all()}


async def _enrich_exercises_with_load(
    db: AsyncSession,
    user_id: int,
    rx: WorkoutPrescription,
    block_context: BlockContext,
    *,
    as_of: datetime | None = None,
) -> list[strength_decline_service.StrengthDeclineShadowPayload]:
    """ADR-0045: resolve %e1RM → suggested kg for prescribed lifts with a qualifying e1RM.

    Mutates ``rx.exercises`` in place. Lifts without a mapped e1RM benchmark, or without
    evidence that qualifies at ``as_of`` (S2: permitted, characterized, and performed
    within the freshness window), keep RPE-only autoregulation (the existing
    ``load_note``). ``as_of`` defaults to now; a caller that must agree with another
    e1RM reader passes the same instant.

    INT-02 (ADR-0066): the e1RM basis is the candidate-aware basis when
    ``DECLINE_CANDIDATE_PRESCRIPTION_BASIS`` is ``on`` (canonical current capacity
    capped by an active decline-candidate ceiling — the selected observation is no
    longer authority); ``shadow`` records both bases but still prescribes off legacy;
    ``off`` prescribes off the S2-selected observation (passed to the resolver as
    ``latest_raw``, a name that predates S2).

    N1: every exercise also gets its ``load_explanation`` (see :func:`_explain_loads`),
    evaluated at the same instant as the selection, and set before any early return — a
    lift with no qualifying evidence is exactly the one the athlete needs told about.

    Returns the shadow payloads the caller must persist **after** the prescription
    commits. This function performs no shadow I/O itself: it runs inside the
    prescription's transaction, and a telemetry write staged there would be committed
    — or fail — together with the prescription.
    """
    shadow_payloads: list[strength_decline_service.StrengthDeclineShadowPayload] = []
    if not rx.exercises:
        return shadow_payloads
    reference = as_of or datetime.now(UTC)
    code_by_name = await _e1rm_codes_for_names(db, [ex.name for ex in rx.exercises])
    e1rm_by_code, selections = await _current_e1rm_values(
        db, user_id, set(code_by_name.values()), as_of=reference
    )
    await _explain_loads(db, rx, code_by_name, selections, evaluated_at=reference)
    if not e1rm_by_code:
        return shadow_payloads

    mode = getattr(
        feature_flags, "DECLINE_CANDIDATE_PRESCRIPTION_BASIS",
        strength_decline_service.BASIS_MODE_OFF,
    )
    current_axis: float | None = None
    rules_by_code: dict[str, dict[str, Any]] = {}
    if mode != strength_decline_service.BASIS_MODE_OFF:
        # 2B1: this axis selects the e1RM basis that sizes load. A reconstruction here
        # would size training from the lossy legacy mirror, silently.
        try:
            state = await load_current_state_strict(db, user_id)
        except EngineStateDecodeError as exc:
            raise CanonicalStateInvalid(
                capability="prescription",
                normalized_reason=normalize_decode_error(exc),
            ) from exc
        current_axis = float(state.capacity_x.max_strength) if state is not None else None
        rules_by_code = await _standardization_rules_for_codes(db, set(code_by_name.values()))

    baseline_rpe_cap = _envelope_rpe_cap(block_context)
    # Uncertainty may make this session more cautious. Reads the confidence already
    # attached to `rx.why` by finalize_prescription in phase 3, so the cap is driven by
    # exactly the certainty the athlete is shown — not a second, separately-derived view.
    conservatism = uncertainty_conservatism.decide(
        baseline_rpe_cap=baseline_rpe_cap,
        weakest_status=(
            rx.why.confidence.weakest_capacity_status
            if rx.why is not None and rx.why.confidence is not None
            else None
        ),
        mode=getattr(
            feature_flags, "UNCERTAINTY_CONSERVATISM", uncertainty_conservatism.MODE_OFF
        ),
    )
    if rx.why is not None:
        rx.why.conservatism = ConservatismSummary(
            mode=conservatism.mode,
            applied=conservatism.applied,
            basis_status=conservatism.basis_status,
            baseline_rpe_cap=conservatism.baseline_rpe_cap,
            effective_rpe_cap=conservatism.effective_rpe_cap,
            reason=conservatism.reason,
        )
    rpe_cap = conservatism.effective_rpe_cap
    for ex in rx.exercises:
        code = code_by_name.get(ex.name)
        e1rm = e1rm_by_code.get(code) if code else None
        if e1rm is None:
            continue
        basis = e1rm
        if mode != strength_decline_service.BASIS_MODE_OFF and code:
            decision = await strength_decline_service.resolve_prescription_basis(
                db, user_id, code=code, latest_raw=e1rm, current_axis=current_axis,
                rules=rules_by_code.get(code), mode=mode,
            )
            basis = decision.selected_basis  # legacy in shadow; candidate-aware in on
            if decision.shadow_payload is not None:
                shadow_payloads.append(decision.shadow_payload)
        reps = _first_int(ex.reps) or 5
        pct = sc.percent_1rm_for_prescription(reps, rpe_cap).value
        load = sc.suggested_load_kg(basis, reps, rpe_cap)
        ex.percent_e1rm = round(pct, 3)
        ex.prescribed_load_kg = load
        ex.rpe_cap = rpe_cap
        ex.e1rm_basis_kg = round(basis, 1)
        ex.load_note = f"~{load:g} kg · {round(pct * 100)}% e1RM · cap RPE {rpe_cap:g}"
    return shadow_payloads


@dataclass(frozen=True)
class _PrescriptionContext:
    """Everything read from the athlete's context before scoring.

    The gather phase of prescribe-and-persist, separated from scoring, persistence,
    and the best-effort telemetry tail so each boundary is a checkable seam.
    ``candidate_log`` is created empty here and filled in place by the scorer; the
    telemetry phase reads it back (the frozen field holds the list reference, the
    list itself is mutable).
    """

    target_session: PlannedSession | None
    block_context: BlockContext
    effective_goal: str
    recent: list[dict[str, Any]]
    kpi_summary: dict[str, float]
    active_weak_points: list[str]
    equipment: list[str] | None
    #: A tie-break among movements the athlete can do (S-C); [] = no preference.
    equipment_preference: list[str]
    #: Catalog snapshot the slot resolver selects from (ADR-0016). Loaded once per
    #: prescription so the pure logic layer never touches the database.
    catalog: list[CatalogExercise]
    candidate_log: list[SessionCandidate]
    readiness_override: float | None
    readiness_audit: dict[str, Any]


def _or_default(value: float | None, default: float) -> float:
    """Guard a column the ORM types as non-optional but Postgres leaves nullable.

    `Exercise.skill_demand` is declared `Mapped[float]` yet the migration made it nullable, so
    a row inserted outside the seeder can legally be NULL. The type checker cannot see that;
    the database can. Note this is NOT `value or default` — a genuine 0.0 must survive.
    """
    return default if value is None else value


async def _load_exercise_catalog(db: AsyncSession) -> list[CatalogExercise]:
    """Snapshot the movement catalog as plain data for slot resolution.

    Read whole rather than filtered: the catalog is small (~181 rows) and the resolver needs
    the full set to rank within, so one query beats a query per slot. Returned as frozen
    dataclasses so nothing downstream can hold a live ORM instance across a shadow write.
    """
    rows = (await db.execute(select(Exercise))).scalars().all()
    return [
        CatalogExercise(
            name=row.name,
            modality=row.modality,
            movement_pattern=row.movement_pattern,
            load_type=row.load_type,
            pattern_family=row.pattern_family,
            equipment_required=tuple(row.equipment_required or ()),
            sport_domains=tuple(row.sport_domains or ()),
            weak_point_tags=tuple(row.weak_point_tags or ()),
            skill_demand=_or_default(row.skill_demand, 0.5),
            e1rm_benchmark_code=row.e1rm_benchmark_code,
        )
        for row in rows
    ]


async def _gather_prescription_context(
    db: AsyncSession,
    user_id: int,
    goal: TrainingGoal | None,
    planned_session: PlannedSession | None,
) -> _PrescriptionContext:
    """Phase 2 — read all athlete context/signals needed to score. No writes."""
    # Fetch active (unresolved) weak-point tags for context injection
    wp_result = await db.execute(
        select(WeakPoint.tag).where(
            WeakPoint.user_id == user_id,
            WeakPoint.resolved_at.is_(None),
        )
    )
    active_weak_points = [row[0] for row in wp_result.all()]

    # Resolve the session to prescribe into, then its owning block.
    # get_today_session is the single canonical "today's target session" resolver
    # (user-wide, PENDING, ORDER BY id ASC). /planning/today passes the session it
    # resolved that way straight in; /next-session resolves it here through the same
    # function — so both entry points target the same row by construction, with no
    # nondeterministic tie-break (CONTEXT.md: prescribe-and-persist).
    if planned_session is not None:
        target_session: PlannedSession | None = planned_session
    else:
        target_session = await get_today_session(db, user_id)

    if target_session is not None:
        # Context comes from the session's own block, not a guessed "latest active"
        # block — robust when today's session lives outside the latest active block.
        block_result = await db.execute(
            select(MesocycleBlock).where(MesocycleBlock.id == target_session.block_id)
        )
        active_block = block_result.scalars().first()
    else:
        # No session scheduled today: still let the latest active block inform the
        # day's goal/context (ADR-0030).
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

    block_context: BlockContext = BlockContext()
    if active_block:
        # Adherence is a property of the BLOCK, not of today's slot, so it loads
        # whenever a block is active. Gating it on `target_session` made it
        # unreachable in precisely the situation it exists for: the athlete
        # finishes today's session — which leaves nothing PENDING, so
        # `get_today_session` returns None — reports how it went, and asks what to
        # do next. The `else` branch above already resolves the active block for
        # that case; this is where that resolution is finally used.
        #
        # Deliberately the ONLY field lifted out of the guard below. The others are
        # either slot-specific, or would change goal precedence
        # (`resolve_effective_goal` ranks `block_goal` above the `goal` query
        # param), which is not this change's business.
        block_context.update(**await block_adherence_signals(db, user_id, active_block.id))
    if active_block and target_session:
        block_context.update(
            block_goal=active_block.goal.value,
            session_category=target_session.category,
            session_domain=target_session.domain,
            intensity=active_block.intensity,
            is_deload=target_session.is_deload,
            is_benchmark=target_session.is_benchmark,
            week_number=target_session.week_number,
            duration_weeks=active_block.duration_weeks,
            deload_every_n_weeks=active_block.deload_every_n_weeks,
            deload_volume_factor=active_block.deload_volume_factor,
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

    # ADR-0052: acute wellness may transparently nudge the plan through the readiness
    # *score* channel (bounded by WELLNESS_WEIGHT). Confidence is computed here too but is
    # REPORT-ONLY — it is logged for P13 shadow history and never gates the prescription.
    readiness = await readiness_service.compute_readiness(db, user_id)
    readiness_override = None if readiness.score is None else readiness.score / 100.0
    readiness_audit = _readiness_audit(readiness, readiness_override)

    return _PrescriptionContext(
        target_session=target_session,
        block_context=block_context,
        effective_goal=effective_goal,
        recent=recent,
        kpi_summary=kpi_summary,
        active_weak_points=active_weak_points,
        equipment=(profile.equipment if profile else None),
        equipment_preference=(list(profile.equipment_preference or []) if profile else []),
        catalog=await _load_exercise_catalog(db),
        # candidate_log_out captures the full ranked pool for decision telemetry
        # (Workstream B). It starts empty; the scorer only fills it, never reads it.
        candidate_log=[],
        readiness_override=readiness_override,
        readiness_audit=readiness_audit,
    )


def _score_prescription(
    state: UnifiedStateVector, ctx: _PrescriptionContext
) -> WorkoutPrescription:
    """Phase 3 — score the next session, filling ``ctx.candidate_log`` in place."""
    return recommend_next_session(
        state,
        # Block goals aren't 1:1 with TrainingGoal; the prescriber resolves any
        # goal string to a canonical domain (ADR-0038), so the cast is safe.
        goal=cast(TrainingGoal, ctx.effective_goal),
        recent_sessions=ctx.recent,
        kpi_summary=ctx.kpi_summary or None,
        active_weak_points=ctx.active_weak_points or None,
        available_equipment=ctx.equipment,
        block_context=cast(dict[str, Any] | None, ctx.block_context),
        candidate_log_out=ctx.candidate_log,
        readiness_override=ctx.readiness_override,
        catalog=ctx.catalog,
        equipment_preference=ctx.equipment_preference or None,
    )


async def _persist_prescription(
    db: AsyncSession, target_session: PlannedSession | None, rx: WorkoutPrescription
) -> None:
    """Phase 5 — the production commit: persist ``rx`` into the planned-session slot."""
    if target_session is not None:
        target_session.prescribed_content = rx.to_prescribed_content()
        await db.commit()


async def _record_prescription_telemetry(
    db: AsyncSession,
    user_id: int,
    rx: WorkoutPrescription,
    state: UnifiedStateVector,
    ctx: _PrescriptionContext,
    shadow_payloads: list[strength_decline_service.StrengthDeclineShadowPayload],
) -> None:
    """Phase 6 — best-effort capture, run strictly AFTER the production commit.

    A telemetry failure must never alter or block ``rx``. Each writer is independently
    isolated, so one failing does not stop the next being attempted:
    ``persist_prescription_decision`` swallows its own errors via ``best_effort_write``,
    and the strength-decline + MPC shadow writers run in their own transactions and
    swallow theirs. Order is preserved from the original inline tail. The decline
    payloads were resolved during enrichment; resolving never writes.
    """
    # INT-02 decline-shadow rows.
    for payload in shadow_payloads:
        await strength_decline_service.persist_strength_decline_shadow_best_effort(payload)

    # Decision telemetry.
    await persist_prescription_decision(
        db,
        user_id,
        rx,
        ctx.candidate_log,
        goal=str(ctx.effective_goal),
        decision_mode="adaptive",
        planned_session_id=ctx.target_session.id if ctx.target_session is not None else None,
        state_snapshot=state.model_dump(mode="json"),
        block_context={**dict(ctx.block_context), "readiness_audit": ctx.readiness_audit},
    )

    # Shadow MPC (ADR-0042): re-rank the same candidate pool by receding-horizon
    # lookahead and log MPC-vs-greedy. Capture-only — never alters `rx`.
    await record_mpc_shadow(db, user_id, state, ctx.candidate_log, str(ctx.effective_goal))


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

    Six phases, each its own seam: (1) load/gate state, (2) gather context, (3) score,
    (4) enrich prescribed load, (5) persist the production commit, (6) best-effort
    telemetry — run strictly after the commit so a telemetry failure can never alter or
    block ``rx``.

    When ``planned_session`` is supplied (e.g. the planning route passes the session it
    will display), the prescription is persisted into exactly that slot and block context
    is taken from its owning block — so the displayed session is always the persisted one.
    Otherwise the target is today's pending session (get_today_session).
    """
    # Phase 1 — load/gate state. Baseline AthleteState is created only for an athlete
    # who has none. An existing athlete whose state is damaged is refused here — before
    # any sizing, revision, or commitment — not prescribed from a legacy reconstruction
    # (INT-15 2B1).
    try:
        state = await load_or_init_current_state_strict(db, user_id)
    except EngineStateDecodeError as exc:
        raise CanonicalStateInvalid(
            capability="prescription",
            normalized_reason=normalize_decode_error(exc),
        ) from exc

    # Phase 2 — gather athlete context/signals (read-only).
    ctx = await _gather_prescription_context(db, user_id, goal, planned_session)

    # Phase 3 — score (fills ctx.candidate_log).
    rx = _score_prescription(state, ctx)

    # Phase 4 — ADR-0045: strength prescriptions speak in load — resolve %e1RM →
    # suggested kg (+ RPE cap) before persisting. Resolves shadow payloads; no writes.
    shadow_payloads = await _enrich_exercises_with_load(db, user_id, rx, ctx.block_context)
    # Loads were just written onto the exercises, so the structure attached in finalize is
    # stale. Re-derive it here rather than editing both views — the model validator refuses a
    # prescription whose structure and exercises disagree, which is what keeps them one thing.
    rx.structure = structure_from_exercises(rx.exercises)
    # Explanation #4: which of the athlete's own flagged deficits this session addresses.
    await _enrich_exercises_with_weak_point_tags(db, rx, ctx.active_weak_points)

    # Phase 5 — persist the prescription (the production commit).
    await _persist_prescription(db, ctx.target_session, rx)

    # Phase 6 — best-effort telemetry, strictly after the commit.
    await _record_prescription_telemetry(db, user_id, rx, state, ctx, shadow_payloads)

    return rx
