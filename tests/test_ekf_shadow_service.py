"""Integration tests for the shadow EKF service (ADR-0041).

Verify that workout ingest and benchmark assimilation write ``ekf_shadow_log`` rows in a
proper filter chain, that updates shrink uncertainty, and — critically — that a failure
in the shadow path never touches production state or the caller's result.

Requires a live PostgreSQL instance (async_db fixture).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from psd_helpers import assert_covariance_psd
from sqlalchemy import func, select

from app.analysis.feature_builders.ekf_calibration_features import summarize_ekf_shadow
from app.logic.ekf.wellness_input import build_wellness_shadow_input
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.ekf_shadow import EkfShadowLog
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.models.wellness import WellnessSample
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
    # Shadow-only may log rather than abort on violation, but gets the same
    # numerical classification as the rest of the EKF — no looser tolerance.
    assert_covariance_psd(cov)


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


async def test_wellness_observation_writes_fatigue_update_row(async_db):
    user = await _create_user(async_db)
    await initialize_athlete_state(async_db, user.id)
    sample = WellnessSample(user_id=user.id, date=date(2026, 1, 1), source="manual", soreness=7.0)
    async_db.add(sample)
    await async_db.commit()
    await async_db.refresh(sample)
    si = build_wellness_shadow_input(user.id, sample.id, 7.0)
    outcome = await ekf_shadow_service.record_ekf_wellness_observation(
        async_db, user.id, si, observed_at=datetime.now(UTC)
    )
    assert outcome == "assimilated"
    rows = [r for r in await _ekf_rows(async_db, user.id) if r.event_type == "update"]
    assert len(rows) == 1
    assert rows[0].benchmark_code == "wellness"
    assert rows[0].n_obs == 2  # muscular + structural fatigue (soreness only)
    assert rows[0].trace_post < rows[0].trace_pre
    assert rows[0].source_wellness_sample_id == sample.id  # AUD-C8 idempotency linkage


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


async def test_summarize_ekf_shadow_returns_trace_and_calibration(async_db):
    user = await _create_user(async_db)
    base = await initialize_athlete_state(async_db, user.id)
    for i in (1, 2):
        await process_new_workout(async_db, user.id, _log(base.timestamp + timedelta(hours=24 * i)))
    specs = [ekf_shadow_service.MappingSpec(target_vector="capacity", target_key="max_strength", coefficient=1.0)]
    await record_ekf_update(
        async_db, user.id, benchmark_code="1rm", mapping_specs=specs,
        score01=0.8, observed_at=datetime.now(UTC),
    )

    summary = await summarize_ekf_shadow(async_db, user.id)
    assert summary["n_predict"] == 2
    assert summary["n_update"] == 1
    assert len(summary["trace_series"]) == 3
    assert all(isinstance(t["trace"], float) and t["trace"] > 0 for t in summary["trace_series"])
    assert summary["nis_series"][0]["benchmark_code"] == "1rm"
    assert "verdict" in summary["calibration"]  # too few updates → stay_shadow, but well-formed


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
