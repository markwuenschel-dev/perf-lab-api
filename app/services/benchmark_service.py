from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.engine.state_bridge import athlete_state_kwargs_from_unified, unified_from_athlete_row
from app.logic.state_update_v0 import apply_benchmark_observation
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.models.weak_point import WeakPoint, WeakPointSource
from app.schemas.benchmarks import BenchmarkObservationCreate, BenchmarkObservationRead
from app.services import state_service

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

    now = datetime.now(timezone.utc)

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

    obs = BenchmarkObservation(
        user_id=user_id,
        benchmark_definition_id=definition.id,
        observed_at=body.observed_at or datetime.utcnow(),
        raw_value=body.raw_value,
        secondary_value=body.secondary_value,
        normalized_value=body.normalized_value,
        bodyweight_kg=body.bodyweight_kg,
        rpe=body.rpe,
        heart_rate_avg=body.heart_rate_avg,
        heart_rate_drift_pct=body.heart_rate_drift_pct,
        notes=body.notes,
        protocol_metadata=body.protocol_metadata,
        validity_status=body.validity_status,
        source=body.source,
    )
    db.add(obs)
    await db.flush()

    mappings = list(definition.observation_mappings or [])
    if body.validity_status == "valid" and mappings:
        st = await db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )
        last = st.scalars().first()
        if not last:
            current = await state_service.initialize_athlete_state(db, user_id)
        else:
            current = unified_from_athlete_row(last)

        # Use the observation's own timestamp so state history stays chronologically correct
        observation_time = body.observed_at or datetime.utcnow()

        new_state = apply_benchmark_observation(
            current,
            raw_value=body.raw_value,
            normalized_value=body.normalized_value,
            better_direction=definition.better_direction,
            observation_weight=float(definition.observation_weight),
            mappings=mappings,
            observed_at=observation_time,
        )
        kwargs = athlete_state_kwargs_from_unified(new_state)
        db.add(AthleteState(user_id=user_id, **kwargs))

    # Weak-point feedback: flag deficits, resolve improvements
    if body.validity_status == "valid" and body.normalized_value is not None:
        await _apply_weak_point_feedback(
            db, user_id, definition, body.normalized_value, obs.id
        )

    await db.commit()

    # Auto-recompute derived KPI metrics so the dashboard is immediately fresh
    from app.services import dashboard_service as _ds
    try:
        await _ds.recompute_derived_metrics(db, user_id)
    except Exception:
        # Non-critical: KPI recompute failure should not fail the observation write
        pass
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
