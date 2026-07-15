from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.logic.ekf.observation import mapping_specs_from_orm
from app.logic.state_update_v0 import (
    apply_benchmark_observation,
    capacity_increased,
    floor_capacity_at_prior,
    normalize_score01,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.weak_point import WeakPoint, WeakPointSource
from app.models.workout_log import WorkoutLog
from app.models.workout_set_log import WorkoutSetLog
from app.schemas.benchmarks import BenchmarkObservationCreate, BenchmarkObservationRead
from app.services import (
    capacity_floor_shadow_service,
    state_service,
    strength_decline_service,
)

logger = logging.getLogger(__name__)


def _capacity_changed(prior: Any, updated: Any, *, eps: float = 1e-9) -> bool:
    """True iff any capacity axis differs between two states (up or down)."""
    from app.domain.vectors import CapacityState

    return any(
        abs(float(getattr(updated.capacity_x, k)) - float(getattr(prior.capacity_x, k))) > eps
        for k in CapacityState.KEYS
    )

# Normalized score thresholds for weak-point feedback.
# Below DEFICIT → flag as a weakness; above IMPROVEMENT → resolve the weakness.
_DEFICIT_THRESHOLD = 40.0
_IMPROVEMENT_THRESHOLD = 65.0

# Maps BenchmarkDefinition.state_targets capacity axes → canonical weak-point tags.
_STATE_TARGET_TO_WP_TAGS: dict[str, list[str]] = {
    "aerobic": ["aerobic_base"],
    "max_strength": ["squat_pattern", "hip_hinge"],
    "hypertrophy": ["posterior_chain"],
    "work_capacity": ["work_capacity"],
    "skill": ["barbell_technique"],
    "mobility": ["hip_mobility"],
    "anaerobic": ["anaerobic_capacity", "lactate_threshold"],
}


async def _apply_weak_point_feedback(
    db: AsyncSession,
    user_id: int,
    definition: BenchmarkDefinition,
    normalized_value: float | None,
    observation_id: int,
) -> None:
    """
    Create or resolve WeakPoint rows based on a valid benchmark result.

    - normalized_value < DEFICIT_THRESHOLD  → flag (or refresh) a benchmark WeakPoint
    - normalized_value > IMPROVEMENT_THRESHOLD → resolve matching benchmark WeakPoints
    - Between thresholds or None → no change
    """
    if normalized_value is None:
        return

    state_targets: list[str] = list(definition.state_targets or [])
    tags: list[str] = []
    for target in state_targets:
        tags.extend(_STATE_TARGET_TO_WP_TAGS.get(target, []))
    if not tags:
        return

    now = datetime.now(UTC)

    if normalized_value < _DEFICIT_THRESHOLD:
        # Flag each tag as a benchmark-sourced weakness (skip if already active)
        for tag in tags:
            existing = (await db.execute(
                select(WeakPoint).where(
                    WeakPoint.user_id == user_id,
                    WeakPoint.tag == tag,
                    WeakPoint.source == WeakPointSource.BENCHMARK,
                    WeakPoint.resolved_at.is_(None),
                )
            )).scalars().first()
            if existing:
                # Refresh confidence with latest observation data
                existing.confidence = 0.9
                existing.note = (
                    f"Benchmark deficit detected (normalized={normalized_value:.1f}). "
                    f"Definition: {definition.code}"
                )
            else:
                db.add(WeakPoint(
                    user_id=user_id,
                    tag=tag,
                    source=WeakPointSource.BENCHMARK,
                    confidence=0.9,
                    note=(
                        f"Benchmark deficit detected (normalized={normalized_value:.1f}). "
                        f"Definition: {definition.code}"
                    ),
                    detected_at=now,
                ))

    elif normalized_value > _IMPROVEMENT_THRESHOLD:
        # Resolve any active benchmark-sourced WeakPoints for these tags
        result = await db.execute(
            select(WeakPoint).where(
                WeakPoint.user_id == user_id,
                WeakPoint.tag.in_(tags),
                WeakPoint.source == WeakPointSource.BENCHMARK,
                WeakPoint.resolved_at.is_(None),
            )
        )
        for wp in result.scalars().all():
            wp.resolved_at = now
            wp.note = (
                (wp.note or "") +
                f" | Resolved by benchmark improvement "
                f"(normalized={normalized_value:.1f}, code={definition.code})"
            )

    await db.flush()


async def list_definitions(db: AsyncSession) -> list[BenchmarkDefinition]:
    r = await db.execute(
        select(BenchmarkDefinition).order_by(
            BenchmarkDefinition.domain,
            BenchmarkDefinition.code,
        )
    )
    return list(r.scalars().all())


async def list_observations(
    db: AsyncSession,
    user_id: int,
    *,
    benchmark_code: str | None = None,
    limit: int = 100,
) -> list[BenchmarkObservationRead]:
    stmt = (
        select(BenchmarkObservation, BenchmarkDefinition.code)
        .join(BenchmarkDefinition)
        .where(BenchmarkObservation.user_id == user_id)
    )
    if benchmark_code:
        stmt = stmt.where(BenchmarkDefinition.code == benchmark_code)
    stmt = stmt.order_by(BenchmarkObservation.observed_at.desc()).limit(limit)
    result = await db.execute(stmt)
    out: list[BenchmarkObservationRead] = []
    for obs, code in result.all():
        out.append(
            BenchmarkObservationRead(
                id=obs.id,
                user_id=obs.user_id,
                benchmark_definition_id=obs.benchmark_definition_id,
                benchmark_code=code,
                observed_at=obs.observed_at,
                raw_value=obs.raw_value,
                secondary_value=obs.secondary_value,
                normalized_value=obs.normalized_value,
                validity_status=obs.validity_status,
                source=obs.source,
            )
        )
    return out


def _resolve_authority(
    body: BenchmarkObservationCreate, definition: BenchmarkDefinition
) -> dict[str, object]:
    """Derive the full five-dimension provenance + policy capacity authority (ADR-0058).

    ``capacity_effect`` is resolved as the meet of independent caps (source /
    collection_mode / evidence / protocol) narrowed by any caller request — a caller
    may only ever *narrow*, never elevate. The legacy ADR-0055 booleans
    (``affects_capacity`` / ``can_regress_capacity``) are DERIVED from the resolved
    effect, never the reverse. Rejected writer source_types are refused here.
    """
    source_type = body.source_type or oa.default_source_type(body.source)
    if source_type in oa.REJECTED_SOURCE_TYPES:
        raise ValueError(
            f"source_type {source_type!r} is not an accepted writer yet "
            "(no validated writer/UX/tests) — ADR-0058"
        )
    collection_mode = body.collection_mode or oa.default_collection_mode(body.source)

    is_workout = source_type == oa.ST_WORKOUT_EXTRACTION
    if is_workout:
        evidence_type = body.evidence_type or se.EV_ESTIMATED_FROM_TRAINING_SET
        value_semantics = body.value_semantics or se.VS_ESTIMATED
        observation_model = body.observation_model or "workout_e1rm_extraction_v1"
        actor_type = "system"
    else:
        evidence_type = body.evidence_type or se.EV_DIRECT_MEASUREMENT
        value_semantics = body.value_semantics or se.VS_MEASURED
        observation_model = body.observation_model or "benchmark_protocol"
        actor_type = "athlete"

    protocol_validity = oa.derive_protocol_validity(
        has_standardization_rules=definition.standardization_rules is not None,
        value_semantics=value_semantics,
        raw_value_present=True,  # raw_value is required on BenchmarkObservationCreate
    )
    resolution = oa.resolve_authority(
        source_type=source_type,
        collection_mode=collection_mode,
        evidence_type=evidence_type,
        value_semantics=value_semantics,
        protocol_validity=protocol_validity,
        requested_capacity_effect=body.requested_capacity_effect,
    )
    if resolution.over_request_clamped:
        logger.warning(
            "capacity_effect over-request clamped: user requested=%s -> resolved=%s (%s)",
            body.requested_capacity_effect, resolution.capacity_effect, resolution.resolution_reason,
        )
    flags = resolution.legacy_flags()
    affects_prescription = (
        body.affects_prescription if body.affects_prescription is not None else True
    )
    return {
        "source_type": source_type,
        "collection_mode": collection_mode,
        "actor_type": actor_type,
        "provenance_operation": oa.OP_LIVE_WRITE,
        "evidence_type": evidence_type,
        "value_semantics": value_semantics,
        "observation_model": observation_model,
        "protocol_code": definition.code,
        "protocol_validity": protocol_validity,
        "requested_capacity_effect": resolution.requested_capacity_effect,
        "capacity_effect": resolution.capacity_effect,
        "authority_policy_version": resolution.policy_version,
        "authority_resolution_reason": resolution.resolution_reason,
        "affects_capacity": flags["affects_capacity"],
        "can_regress_capacity": flags["can_regress_capacity"],
        "affects_prescription": affects_prescription,
        "confidence_source": body.confidence_source,
        "confidence_model_version": body.confidence_model_version,
    }


async def _verify_log_fk_ownership(
    db: AsyncSession, user_id: int, body: BenchmarkObservationCreate
) -> None:
    """Verify the caller owns any log rows this observation references (INT-A7).

    ``workout_log_id`` / ``set_log_id`` are caller-supplied. Unverified, an athlete
    can attach observations to another athlete's log rows (IDOR — cross-tenant FK
    pollution). Mirrors ``session_feedback_service.create_feedback``: 404, because
    the resource does not exist *for this user*.

    MUST be called before the first write — ``create_observation`` commits its own
    transaction, so a row added before this check could not be rolled back.
    """
    if body.workout_log_id is not None:
        workout_log = (
            await db.execute(
                select(WorkoutLog.id).where(
                    WorkoutLog.id == body.workout_log_id,
                    WorkoutLog.user_id == user_id,
                )
            )
        ).scalars().first()
        if workout_log is None:
            raise HTTPException(status_code=404, detail="Workout log not found")

    # WorkoutSetLog has no user_id — ownership is transitive via its parent log.
    if body.set_log_id is not None:
        set_log = (
            await db.execute(
                select(WorkoutSetLog.id)
                .join(WorkoutLog, WorkoutSetLog.workout_log_id == WorkoutLog.id)
                .where(
                    WorkoutSetLog.id == body.set_log_id,
                    WorkoutLog.user_id == user_id,
                )
            )
        ).scalars().first()
        if set_log is None:
            raise HTTPException(status_code=404, detail="Set log not found")


async def create_observation(
    db: AsyncSession,
    user_id: int,
    body: BenchmarkObservationCreate,
) -> BenchmarkObservationRead:
    r = await db.execute(
        select(BenchmarkDefinition)
        .options(selectinload(BenchmarkDefinition.observation_mappings))
        .where(BenchmarkDefinition.code == body.benchmark_code)
    )
    definition = r.scalars().first()
    if not definition:
        raise ValueError(f"Unknown benchmark code: {body.benchmark_code}")
    if definition.is_derived_only:
        raise ValueError("Observations cannot target derived-only benchmark definitions")

    # Ownership gate (INT-A7) — before the db.add/flush below, and well before the
    # commit at the end of this function, which nothing downstream can roll back.
    await _verify_log_fk_ownership(db, user_id, body)

    # Backend-owned normalization (ADR-0034): derive a [0,1] score from the
    # definition's standardization_rules; expose it as a 0-100 normalized_value when
    # the client didn't supply one. score01 drives the residual capacity anchor.
    score01 = normalize_score01(definition.better_direction, body.raw_value, definition.standardization_rules)
    normalized_value = body.normalized_value
    if normalized_value is None and score01 is not None:
        normalized_value = round(score01 * 100.0, 2)

    authority = _resolve_authority(body, definition)
    obs = BenchmarkObservation(
        user_id=user_id,
        benchmark_definition_id=definition.id,
        observed_at=body.observed_at or datetime.now(UTC).replace(tzinfo=None),
        raw_value=body.raw_value,
        secondary_value=body.secondary_value,
        normalized_value=normalized_value,
        bodyweight_kg=body.bodyweight_kg,
        rpe=body.rpe,
        heart_rate_avg=body.heart_rate_avg,
        heart_rate_drift_pct=body.heart_rate_drift_pct,
        notes=body.notes,
        protocol_metadata=body.protocol_metadata,
        validity_status=body.validity_status,
        source=body.source,
        exercise_id=body.exercise_id,
        workout_log_id=body.workout_log_id,
        set_log_id=body.set_log_id,
        reps=body.reps,
        load_kg=body.load_kg,
        rir=body.rir,
        formula=body.formula,
        effort_fidelity=body.effort_fidelity,
        confidence=body.confidence,
        observation_weight=body.observation_weight,
        model_version=body.model_version,
        **authority,
    )
    db.add(obs)
    await db.flush()

    mappings = list(definition.observation_mappings or [])
    observation_time = body.observed_at or datetime.now(UTC).replace(tzinfo=None)
    # Policy-derived capacity authority (ADR-0058): the resolved capacity_effect is
    # the state-transition operator. Re-derive it from provenance fail-closed and
    # take the stricter of stored-vs-law — a mismarked row can never earn authority
    # it wasn't granted. Four distinct handlers, not one residual path behind flags:
    #   bidirectional_update → full signed residual (may regress)
    #   upward_lower_bound   → residual then non-regressing capacity floor
    #   initialize_prior     → seed only when no twin exists yet (idempotency guard)
    #   none                 → recorded for history/tracking, never touches capacity
    effect = oa.meet(obs.capacity_effect or oa.CE_NONE, oa.capacity_effect_of(obs))
    is_valid = body.validity_status == "valid" and bool(mappings)
    capacity_authoritative = is_valid and effect == oa.CE_BIDIRECTIONAL_UPDATE
    if mappings and body.validity_status == "valid" and effect == oa.CE_NONE:
        logger.info(
            "capacity update skipped (no policy authority): user=%s code=%s "
            "source_type=%s mode=%s protocol=%s",
            user_id, body.benchmark_code, obs.source_type, obs.collection_mode,
            obs.protocol_validity,
        )
    # Snapshot mapping data for the shadow EKF *before* commit — ORM attributes expire on
    # commit, so lazy-loading them afterward in async would fail. Shadow EKF + weak-point
    # feedback stay gated on measurement-grade (bidirectional) authority.
    ekf_specs = mapping_specs_from_orm(mappings) if capacity_authoritative else []

    # Live promotion boundary: only bidirectional_update (measured, may regress) and
    # initialize_prior (seed an empty twin) mutate canonical state in this slice.
    # upward_lower_bound is fully resolved + recorded, but promoting its floor-ratchet
    # to live capacity is DEFERRED — that would flip the deployed ADR-0055 invariant
    # (a workout-derived estimate never mutates canonical capacity) on the highest-risk
    # path, so it graduates behind a shadow old-vs-new comparison (ADR-0058 deferred).
    apply_state = is_valid and effect in (oa.CE_BIDIRECTIONAL_UPDATE, oa.CE_INITIALIZE_PRIOR)
    if apply_state and effect == oa.CE_INITIALIZE_PRIOR:
        # A prior seeds an uncertain twin; it may not overwrite an established one.
        if await state_service.load_current_state(db, user_id) is not None:
            apply_state = False
    if apply_state:
        current = await state_service.load_or_init_current_state(db, user_id)

        new_state = apply_benchmark_observation(
            current,
            raw_value=body.raw_value,
            normalized_value=normalized_value,
            better_direction=definition.better_direction,
            observation_weight=float(definition.observation_weight),
            mappings=mappings,
            observed_at=observation_time,
            score01=score01,
        )
        if effect in (oa.CE_UPWARD_LOWER_BOUND, oa.CE_INITIALIZE_PRIOR):
            # Non-regressing: clamp capacity axes up to at least their prior. If the
            # lower bound lands below the current watermark it raised nothing — record
            # the observation for history but write no redundant capacity row.
            new_state = floor_capacity_at_prior(current, new_state)
            if not capacity_increased(current, new_state):
                new_state = None
        elif effect == oa.CE_BIDIRECTIONAL_UPDATE:
            # INT-02 (ADR-0066): a single low bidirectional benchmark must not durably
            # regress max_strength. The decline machine holds the axis on first
            # evidence + opens a candidate; applies a bounded decline only once an
            # independent observation confirms it; dismisses on re-demonstration.
            outcome = await strength_decline_service.resolve_bidirectional_observation(
                db, user_id, current=current, observation=obs, definition=definition,
                mappings=mappings, observed_raw=body.raw_value,
            )
            if outcome.intercepted:
                if outcome.apply_posterior is not None:
                    # Confirmed decline: a bounded, auditable downward axis move.
                    obs_state = new_state.capacity_x
                    obs_state.max_strength = outcome.apply_posterior
                elif outcome.hold_axis:
                    strength_decline_service.hold_axis_at_prior(current, new_state)
                if outcome.decline_transition_status is not None:
                    obs.applied_capacity_effect = outcome.applied_capacity_effect
                    obs.decline_transition_status = outcome.decline_transition_status
                if not _capacity_changed(current, new_state):
                    new_state = None
            else:
                # Upward / first-measurement / non-strength: normal bidirectional apply.
                obs.applied_capacity_effect = oa.CE_BIDIRECTIONAL_UPDATE
        if new_state is not None:
            kwargs = athlete_state_kwargs_from_unified(new_state)
            db.add(AthleteState(user_id=user_id, **kwargs))
    elif is_valid and effect == oa.CE_UPWARD_LOWER_BOUND:
        # Deferred floor-ratchet (ADR-0058): the authority is resolved but NOT promoted
        # to a live mutation. Record the candidate — proposed floor, projected uplift,
        # application-policy version, not-applied reason — as shadow evidence, separate
        # from any applied transition. Canonical capacity is untouched.
        current = await state_service.load_or_init_current_state(db, user_id)
        candidate = apply_benchmark_observation(
            current,
            raw_value=body.raw_value,
            normalized_value=normalized_value,
            better_direction=definition.better_direction,
            observation_weight=float(definition.observation_weight),
            mappings=mappings,
            observed_at=observation_time,
            score01=score01,
        )
        floored = floor_capacity_at_prior(current, candidate)
        await capacity_floor_shadow_service.record_floor_candidate(
            db, user_id, observation=obs, benchmark_code=body.benchmark_code,
            prior=current, floored=floored,
        )

    # Weak-point feedback: flag deficits, resolve improvements. Gated on measurement-
    # grade (bidirectional) authority — training-derived / estimated / seeding evidence
    # must not flag/resolve weak points as if it were a measurement (ADR-0055/0058).
    if capacity_authoritative and normalized_value is not None:
        await _apply_weak_point_feedback(
            db, user_id, definition, normalized_value, obs.id
        )

    await db.commit()

    # Shadow EKF (ADR-0041): assimilate this benchmark into the parallel full-covariance
    # belief. Best-effort and capture-only — never affects the returned observation.
    if ekf_specs:
        from app.services import ekf_shadow_service

        await ekf_shadow_service.record_ekf_update(
            db,
            user_id,
            benchmark_code=body.benchmark_code,
            mapping_specs=ekf_specs,
            score01=score01,
            observed_at=observation_time,
        )

    # Auto-recompute derived KPI metrics so the dashboard is immediately fresh
    from app.services import dashboard_service as _ds
    try:
        await _ds.recompute_derived_metrics(db, user_id)
    except Exception:
        # Non-critical: a KPI recompute failure must not fail the observation
        # write, but it should be visible (dashboard may be briefly stale).
        logger.warning(
            "derived-KPI recompute failed for user %s after benchmark write",
            user_id, exc_info=True,
        )
    await db.refresh(obs)
    return BenchmarkObservationRead(
        id=obs.id,
        user_id=obs.user_id,
        benchmark_definition_id=obs.benchmark_definition_id,
        benchmark_code=body.benchmark_code,
        observed_at=obs.observed_at,
        raw_value=obs.raw_value,
        secondary_value=obs.secondary_value,
        normalized_value=obs.normalized_value,
        validity_status=obs.validity_status,
        source=obs.source,
    )
