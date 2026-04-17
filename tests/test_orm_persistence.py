"""ORM persistence integrity tests.

Requires a live PostgreSQL instance (uses async_db fixture from conftest.py).
These tests verify the critical DB-layer invariants:
- Append-only state (Decision 1)
- Timestamp is set from UnifiedStateVector, not DB server time
- Workout logs are in a separate table from athlete states (Decision 2)
- simulate-dose does not create DB rows (Decision 9)
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.athlete_state import AthleteState
from app.models.user import User
from app.services.state_service import initialize_athlete_state, process_new_workout
from app.schemas.workouts import WorkoutLog


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_user(db, email: str = "test@example.com") -> User:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _log(ts: datetime, rpe: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=ts,
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=rpe,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_initialize_athlete_state_creates_exactly_one_row(async_db):
    user = await _create_user(async_db)
    await initialize_athlete_state(async_db, user.id)

    result = await async_db.execute(
        select(func.count()).where(AthleteState.user_id == user.id)
    )
    count = result.scalar()
    assert count == 1


async def test_process_new_workout_appends_new_row(async_db):
    user = await _create_user(async_db)
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    count_before_result = await async_db.execute(
        select(func.count()).where(AthleteState.user_id == user.id)
    )
    count_before = count_before_result.scalar()

    t1 = t_init + timedelta(hours=24)
    await process_new_workout(async_db, user.id, _log(t1))

    count_after_result = await async_db.execute(
        select(func.count()).where(AthleteState.user_id == user.id)
    )
    count_after = count_after_result.scalar()

    assert count_after == count_before + 1, (
        f"Expected count to increase by 1 (append-only), got {count_before} → {count_after}"
    )


async def test_prior_state_row_not_modified(async_db):
    """The original AthleteState row must be untouched after process_new_workout."""
    user = await _create_user(async_db)
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    # Capture first row
    first_result = await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.id.asc())
        .limit(1)
    )
    first_row = first_result.scalars().first()
    original_c_met = first_row.c_met_aerobic
    original_c_nm = first_row.c_nm_force

    # Log a workout with timestamp after the init state
    t1 = t_init + timedelta(hours=24)
    await process_new_workout(async_db, user.id, _log(t1))

    # Re-fetch first row to ensure it wasn't modified
    await async_db.refresh(first_row)
    assert first_row.c_met_aerobic == original_c_met, "First row c_met_aerobic was modified"
    assert first_row.c_nm_force == original_c_nm, "First row c_nm_force was modified"


async def test_new_state_timestamp_matches_log_timestamp(async_db):
    """
    The new AthleteState.timestamp should equal log.timestamp
    (since time_delta = log.timestamp - prev.timestamp, and
    new_state.timestamp = prev.timestamp + time_delta = log.timestamp).
    """
    user = await _create_user(async_db)
    baseline = await initialize_athlete_state(async_db, user.id)
    t_init = baseline.timestamp

    t_log = t_init + timedelta(hours=24)
    await process_new_workout(async_db, user.id, _log(t_log))

    latest = (await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )).scalars().first()

    # Strip tzinfo for comparison (DB stores naive datetime)
    db_ts = latest.timestamp.replace(tzinfo=None) if latest.timestamp.tzinfo else latest.timestamp
    expected_ts = t_log.replace(tzinfo=None)
    diff = abs((db_ts - expected_ts).total_seconds())
    assert diff < 1.0, f"New state timestamp {db_ts} should match log timestamp {expected_ts}"


async def test_simulate_dose_does_not_create_state_row(async_db):
    """
    calculate_stress_dose (simulate-dose) must not write to the DB.
    Decision 9: /simulate-dose is non-mutating.
    """
    from app.logic.dose_engine_v0 import calculate_stress_dose

    user = await _create_user(async_db)

    count_before = (await async_db.execute(
        select(func.count()).where(AthleteState.user_id == user.id)
    )).scalar()

    t0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    calculate_stress_dose(_log(t0))  # pure function — no DB interaction

    count_after = (await async_db.execute(
        select(func.count()).where(AthleteState.user_id == user.id)
    )).scalar()

    assert count_after == count_before, (
        "simulate-dose must not create AthleteState rows (Decision 9)"
    )
