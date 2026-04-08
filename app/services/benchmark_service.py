from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.engine.state_bridge import athlete_state_kwargs_from_unified, unified_from_athlete_row
from app.logic.state_update import apply_benchmark_observation
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.schemas.benchmarks import BenchmarkObservationCreate, BenchmarkObservationRead
from app.services import state_service


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
