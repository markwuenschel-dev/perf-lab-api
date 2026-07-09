from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.logic import strength_evidence as se
from app.logic.ekf.observation import mapping_specs_from_orm
from app.logic.state_update_v0 import apply_benchmark_observation, normalize_score01
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.weak_point import WeakPoint, WeakPointSource
from app.schemas.benchmarks import BenchmarkObservationCreate, BenchmarkObservationRead
from app.services import state_service

logger = logging.getLogger(__name__)

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


def _resolve_authority(body: BenchmarkObservationCreate) -> dict[str, object]:
    """Fill evidence authority/provenance from the caller, defaulting by ``source``.

    A benchmark/manual entry defaults to a capacity-authoritative direct measurement;
    ``workout_extraction`` defaults to an estimated, non-regressing training row that
    never touches capacity. Explicit caller values win — except that
    ``workout_extraction`` can **never** be granted capacity regression (fail-closed).
    """
    is_workout = body.source == se.SOURCE_WORKOUT_EXTRACTION
    if is_workout:
        evidence_type = body.evidence_type or se.EV_ESTIMATED_FROM_TRAINING_SET
        value_semantics = body.value_semantics or se.VS_ESTIMATED
        affects_capacity = False
        can_regress_capacity = False
        affects_prescription = (
            body.affects_prescription if body.affects_prescription is not None else True
        )
        observation_model = body.observation_model or "workout_e1rm_extraction_v1"
    else:
        evidence_type = body.evidence_type or se.EV_DIRECT_MEASUREMENT
        value_semantics = body.value_semantics or se.VS_MEASURED
        affects_capacity = (
            body.affects_capacity if body.affects_capacity is not None else True
        )
        can_regress_capacity = (
            body.can_regress_capacity if body.can_regress_capacity is not None else True
        )
        affects_prescription = (
            body.affects_prescription if body.affects_prescription is not None else True
        )
        observation_model = body.observation_model or "benchmark_protocol"
    return {
        "evidence_type": evidence_type,
        "value_semantics": value_semantics,
        "observation_model": observation_model,
        "affects_capacity": affects_capacity,
        "can_regress_capacity": can_regress_capacity,
        "affects_prescription": affects_prescription,
    }


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

    # Backend-owned normalization (ADR-0034): derive a [0,1] score from the
    # definition's standardization_rules; expose it as a 0-100 normalized_value when
    # the client didn't supply one. score01 drives the residual capacity anchor.
    score01 = normalize_score01(definition.better_direction, body.raw_value, definition.standardization_rules)
    normalized_value = body.normalized_value
    if normalized_value is None and score01 is not None:
        normalized_value = round(score01 * 100.0, 2)

    authority = _resolve_authority(body)
    obs = BenchmarkObservation(
        user_id=user_id,
        benchmark_definition_id=definition.id,
        observed_at=body.observed_at or datetime.utcnow(),
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
    observation_time = body.observed_at or datetime.utcnow()
    # ADR-0055 fail-closed capacity guard: only a protocol-grade, capacity-authoritative
    # observation may enter the bidirectional capacity residual path. Recomputed from
    # provenance here — a mismarked row is still refused. Training-derived e1RM
    # (workout_extraction) is recorded for tracking but never touches canonical capacity.
    capacity_authoritative = se.capacity_authoritative(obs)
    apply_to_state = body.validity_status == "valid" and bool(mappings) and capacity_authoritative
    if mappings and body.validity_status == "valid" and not capacity_authoritative:
        logger.info(
            "capacity update skipped (non-authoritative evidence): user=%s code=%s "
            "source=%s evidence_type=%s",
            user_id, body.benchmark_code, obs.source, obs.evidence_type,
        )
    # Snapshot mapping data for the shadow EKF *before* commit — ORM attributes expire on
    # commit, so lazy-loading them afterward in async would fail.
    ekf_specs = mapping_specs_from_orm(mappings) if apply_to_state else []
    if apply_to_state:
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
        kwargs = athlete_state_kwargs_from_unified(new_state)
        db.add(AthleteState(user_id=user_id, **kwargs))

    # Weak-point feedback: flag deficits, resolve improvements. Gated on capacity
    # authority — training-derived tracking evidence must not flag/resolve weak points
    # as if it were a measurement (ADR-0055).
    if apply_to_state and normalized_value is not None:
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
