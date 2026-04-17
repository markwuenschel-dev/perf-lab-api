"""End-to-end integration tests (service layer, no HTTP).

Requires a live PostgreSQL instance (uses async_db fixture from conftest.py).
Tests the full control loop: init → log workout → state evolution.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.athlete_state import AthleteState
from app.models.user import User
from app.services.state_service import initialize_athlete_state, process_new_workout
from app.schemas.workouts import WorkoutLog


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_user(db, email: str = "integration@example.com") -> User:
    user = User(email=email, hashed_password="hash", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _strength_log(ts: datetime, rpe: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=ts,
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=rpe,
        total_volume_load=4000.0,
        estimated_sets=12.0,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_first_run_scenario(async_db):
    """
    New user → initialize state → log a strength workout → new state appended.
    Fatigue should be higher than baseline.
    """
    user = await _create_user(async_db, "first_run@test.com")
    baseline_state = await initialize_athlete_state(async_db, user.id)
    baseline_fatigue = (
        baseline_state.f_met_systemic
        + baseline_state.f_nm_peripheral
        + baseline_state.f_nm_central
    )

    t1 = baseline_state.timestamp + timedelta(hours=24)
    post_workout_state = await process_new_workout(async_db, user.id, _strength_log(t1, rpe=8.0))
    post_fatigue = (
        post_workout_state.f_met_systemic
        + post_workout_state.f_nm_peripheral
        + post_workout_state.f_nm_central
    )

    assert post_fatigue > baseline_fatigue, (
        f"Fatigue should increase after workout: baseline={baseline_fatigue:.2f}, post={post_fatigue:.2f}"
    )


async def test_repeated_sessions_history_preserved(async_db):
    """
    Three sequential workouts should create exactly 4 rows (1 init + 3 updates)
    with strictly ascending timestamps.
    """
    user = await _create_user(async_db, "repeat@test.com")
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    for i in range(3):
        t_i = t_init + timedelta(days=i + 1)
        await process_new_workout(async_db, user.id, _strength_log(t_i))

    rows = (await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.id.asc())
    )).scalars().all()

    assert len(rows) == 4, f"Expected 4 AthleteState rows (1 init + 3), got {len(rows)}"

    # Timestamps must be strictly ascending
    timestamps = [r.timestamp for r in rows]
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i - 1], (
            f"Timestamps not ascending at index {i}: {timestamps[i-1]} >= {timestamps[i]}"
        )


async def test_negative_dt_clamped_no_crash(async_db):
    """
    If a log.timestamp is earlier than the last state's timestamp (stale/backdated log),
    the time delta is clamped to 0. The state update should succeed and append a new row.
    """
    user = await _create_user(async_db, "backdated@test.com")
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    # Log with a timestamp BEFORE the initial state (simulating a backdated log)
    t_stale = t_init - timedelta(hours=2)
    result = await process_new_workout(async_db, user.id, _strength_log(t_stale))
    assert result is not None, "process_new_workout should succeed even with backdated log"

    rows = (await async_db.execute(
        select(AthleteState).where(AthleteState.user_id == user.id)
    )).scalars().all()
    assert len(rows) == 2, f"Expected 2 rows (init + backdated), got {len(rows)}"


async def test_fatigue_accumulates_across_sessions(async_db):
    """
    Multiple hard sessions without recovery should accumulate fatigue.
    Second post-workout fatigue > first post-workout fatigue.
    """
    user = await _create_user(async_db, "accumulate@test.com")
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    t1 = t_init + timedelta(hours=2)
    state1 = await process_new_workout(async_db, user.id, _strength_log(t1, rpe=9.0))
    f1 = state1.fatigue_f.cns + state1.fatigue_f.muscular + state1.fatigue_f.metabolic

    t2 = t1 + timedelta(hours=2)
    state2 = await process_new_workout(async_db, user.id, _strength_log(t2, rpe=9.0))
    f2 = state2.fatigue_f.cns + state2.fatigue_f.muscular + state2.fatigue_f.metabolic

    assert f2 > f1, (
        f"Fatigue should accumulate across closely-spaced sessions: f1={f1:.2f}, f2={f2:.2f}"
    )
