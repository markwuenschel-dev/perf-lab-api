"""Integration tests for the shadow EKF service (ADR-0041).

Verify that workout ingest and benchmark assimilation write ``ekf_shadow_log`` rows in a
proper filter chain, that updates shrink uncertainty, and — critically — that a failure
in the shadow path never touches production state or the caller's result.

Requires a live PostgreSQL instance (async_db fixture).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import func, select

from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.ekf_shadow import EkfShadowLog
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import WorkoutLog
from app.services import benchmark_service, ekf_shadow_service
from app.services.ekf_shadow_service import record_ekf_update
from app.services.state_service import initialize_athlete_state, process_new_workout

pytestmark = pytest.mark.asyncio


async def _create_user(db, email="ekf@example.com") -> User:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _log(ts: datetime) -> WorkoutLog:
    return WorkoutLog(
        timestamp=ts, modality="Strength", duration_minutes=60.0, session_rpe=7.0,
        sleep_quality=7.0, life_stress_inverse=7.0,
    )


async def _ekf_rows(db, user_id: int) -> list[EkfShadowLog]:
    res = await db.execute(
        select(EkfShadowLog).where(EkfShadowLog.user_id == user_id).order_by(EkfShadowLog.id)
    )
    return list(res.scalars().all())


async def _state_count(db, user_id: int) -> int:
    res = await db.execute(select(func.count()).where(AthleteState.user_id == user_id))
    return int(res.scalar())


async def test_workout_writes_predict_row(async_db):
    user = await _create_user(async_db)
    base = await initialize_athlete_state(async_db, user.id)
    await process_new_workout(async_db, user.id, _log(base.timestamp + timedelta(hours=24)))

    rows = await _ekf_rows(async_db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "predict"
    assert row.decision_impact == "none_shadow_only"
    assert len(row.mean_json) == 22
    cov = np.array(row.covariance_json)
    assert cov.shape == (22, 22)
    assert np.min(np.linalg.eigvalsh(cov)) >= -1e-7  # PSD


async def test_predict_chain_advances_over_multiple_workouts(async_db):
    user = await _create_user(async_db)
    base = await initialize_athlete_state(async_db, user.id)
    for i in (1, 2, 3):
        await process_new_workout(async_db, user.id, _log(base.timestamp + timedelta(hours=24 * i)))
    rows = await _ekf_rows(async_db, user.id)
    assert len(rows) == 3
    assert all(r.event_type == "predict" for r in rows)


async def test_update_shrinks_trace_and_writes_update_row(async_db):
    user = await _create_user(async_db)
    base = await initialize_athlete_state(async_db, user.id)
    await process_new_workout(async_db, user.id, _log(base.timestamp + timedelta(hours=24)))

    specs = [ekf_shadow_service.MappingSpec(target_vector="capacity", target_key="max_strength", coefficient=1.0)]
    await record_ekf_update(
        async_db, user.id, benchmark_code="1rm", mapping_specs=specs,
        score01=0.85, observed_at=datetime.now(UTC),
    )
    rows = await _ekf_rows(async_db, user.id)
    update_rows = [r for r in rows if r.event_type == "update"]
    assert len(update_rows) == 1
    u = update_rows[0]
    assert u.benchmark_code == "1rm"
    assert u.trace_post is not None and u.trace_pre is not None
    assert u.trace_post < u.trace_pre  # a measurement reduces total uncertainty


async def test_benchmark_endpoint_end_to_end_writes_update_and_keeps_production(async_db):
    user = await _create_user(async_db)
    definition = BenchmarkDefinition(
        code="e1rm", name="Estimated 1RM", domain="strength", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 240.0},
    )
    async_db.add(definition)
    await async_db.flush()
    async_db.add(ObservationMapping(
        benchmark_definition_id=definition.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await async_db.commit()

    states_before = await _state_count(async_db, user.id)
    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(benchmark_code="e1rm", raw_value=200.0, validity_status="valid"),
    )

    # Production still assimilated the benchmark (a new state row appended).
    assert await _state_count(async_db, user.id) > states_before
    # And the shadow EKF logged an update row.
    update_rows = [r for r in await _ekf_rows(async_db, user.id) if r.event_type == "update"]
    assert len(update_rows) == 1
    assert update_rows[0].benchmark_code == "e1rm"


async def test_shadow_failure_never_touches_production(async_db, monkeypatch):
    user = await _create_user(async_db)
    uid = user.id  # capture before any rollback expires the ORM object
    base = await initialize_athlete_state(async_db, uid)
    # First workout seeds the belief (predict() is not called on the seed path).
    await process_new_workout(async_db, uid, _log(base.timestamp + timedelta(hours=24)))
    rows_before = len(await _ekf_rows(async_db, uid))
    states_before = await _state_count(async_db, uid)

    def _boom(*a, **k):
        raise RuntimeError("simulated EKF failure")

    monkeypatch.setattr("app.services.ekf_shadow_service.predict", _boom)

    # Second workout: predict() is invoked and raises — must be swallowed.
    result = await process_new_workout(async_db, uid, _log(base.timestamp + timedelta(hours=48)))

    assert result is not None  # caller got its state back
    assert await _state_count(async_db, uid) == states_before + 1  # production row appended
    assert len(await _ekf_rows(async_db, uid)) == rows_before  # no new shadow row
