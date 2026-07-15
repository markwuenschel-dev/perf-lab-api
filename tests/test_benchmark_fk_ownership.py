"""INT-A7 — benchmark observations may not reference another athlete's log rows.

``BenchmarkObservation.workout_log_id`` / ``set_log_id`` are caller-supplied FKs.
Without an ownership check the service persists whatever ids the caller sends, so
athlete B can attach an observation to athlete A's ``WorkoutLog`` / ``WorkoutSetLog``
(cross-tenant FK pollution — a data-integrity defect, not a read leak: the row's own
``user_id`` is still B's and neither column is ever read back).

Mirrors the two-user IDOR pattern in tests/test_session_feedback_routes.py and the
service-level ``async_db`` fixture pattern in tests/test_capacity_corruption_hotfix.py.
Expected shape (copied from session_feedback_service): ``HTTPException`` 404 — the
resource does not exist *for this user* — raised BEFORE any write.
"""
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.models.workout_set_log import WorkoutSetLog
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service

pytestmark = pytest.mark.asyncio


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_definition(db) -> BenchmarkDefinition:
    definition = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=definition.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()
    await db.refresh(definition)
    return definition


async def _mk_workout_log(db, user_id: int) -> WorkoutLog:
    wl = WorkoutLog(
        user_id=user_id,
        session_timestamp=datetime.now(UTC).replace(tzinfo=None),
        modality="strength",
        duration_minutes=60.0,
        session_rpe=7.0,
    )
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return wl


async def _mk_set_log(db, workout_log_id: int) -> WorkoutSetLog:
    sl = WorkoutSetLog(
        workout_log_id=workout_log_id,
        set_index=0,
        free_text_name="Back Squat",
        load_type="barbell",
        load_kg=100.0,
        reps=5,
        rpe=8.0,
        is_top_set=True,
    )
    db.add(sl)
    await db.commit()
    await db.refresh(sl)
    return sl


async def _obs_count(db) -> int:
    r = await db.execute(select(func.count()).select_from(BenchmarkObservation))
    return int(r.scalar_one())


def _body(**kw) -> BenchmarkObservationCreate:
    base = {"benchmark_code": "pl_e1rm_squat", "raw_value": 120.0, "source": "manual"}
    base.update(kw)
    return BenchmarkObservationCreate(**base)


# ── the guard: another athlete's FKs are refused ──────────────────────────────

async def test_rejects_other_users_workout_log(async_db):
    """B may not attach an observation to A's WorkoutLog."""
    await _seed_definition(async_db)
    victim = await _user(async_db, "a7_victim_wl@test.com")
    attacker = await _user(async_db, "a7_attacker_wl@test.com")
    victim_log = await _mk_workout_log(async_db, victim.id)

    before = await _obs_count(async_db)

    with pytest.raises(HTTPException) as exc:
        await benchmark_service.create_observation(
            async_db, attacker.id, _body(workout_log_id=victim_log.id)
        )
    assert exc.value.status_code == 404

    # Refused BEFORE any write — create_observation commits internally, so a row
    # written before the check could never be rolled back.
    async_db.expunge_all()
    assert await _obs_count(async_db) == before


async def test_rejects_other_users_set_log(async_db):
    """B may not attach an observation to A's WorkoutSetLog.

    WorkoutSetLog has no user_id of its own — ownership is transitive through its
    parent WorkoutLog, so the check must join rather than read a column.
    """
    await _seed_definition(async_db)
    victim = await _user(async_db, "a7_victim_sl@test.com")
    attacker = await _user(async_db, "a7_attacker_sl@test.com")
    victim_log = await _mk_workout_log(async_db, victim.id)
    victim_set = await _mk_set_log(async_db, victim_log.id)

    before = await _obs_count(async_db)

    with pytest.raises(HTTPException) as exc:
        await benchmark_service.create_observation(
            async_db, attacker.id, _body(set_log_id=victim_set.id)
        )
    assert exc.value.status_code == 404

    async_db.expunge_all()
    assert await _obs_count(async_db) == before


async def test_rejects_nonexistent_workout_log(async_db):
    """A dangling FK is refused the same way — 404, not a DB integrity error."""
    await _seed_definition(async_db)
    user = await _user(async_db, "a7_dangling@test.com")

    with pytest.raises(HTTPException) as exc:
        await benchmark_service.create_observation(
            async_db, user.id, _body(workout_log_id=999_999)
        )
    assert exc.value.status_code == 404


# ── the guard does not break the legitimate paths ─────────────────────────────

async def test_accepts_own_workout_log_and_set_log(async_db):
    """The caller's own FKs still persist — this is the write-time extraction path."""
    await _seed_definition(async_db)
    user = await _user(async_db, "a7_owner@test.com")
    own_log = await _mk_workout_log(async_db, user.id)
    own_set = await _mk_set_log(async_db, own_log.id)

    out = await benchmark_service.create_observation(
        async_db, user.id,
        _body(workout_log_id=own_log.id, set_log_id=own_set.id),
    )
    assert out.id is not None

    async_db.expunge_all()
    row = (
        await async_db.execute(
            select(BenchmarkObservation).where(BenchmarkObservation.id == out.id)
        )
    ).scalars().first()
    assert row is not None
    assert row.workout_log_id == own_log.id
    assert row.set_log_id == own_set.id


async def test_accepts_observation_with_no_log_fks(async_db):
    """Both FKs are optional — a bare manual benchmark must still work."""
    await _seed_definition(async_db)
    user = await _user(async_db, "a7_nofks@test.com")

    out = await benchmark_service.create_observation(async_db, user.id, _body())
    assert out.id is not None
