"""ADR-0055 hotfix — training-derived e1RM must never regress capacity.

The core invariant under test: **only protocol-grade benchmark observations may
update canonical capacity; workout_extraction may raise a lower-bound floor but can
never enter the bidirectional capacity residual path — even if a row is mismarked.**
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.logic import strength_evidence as se
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services import benchmark_service
from app.services.state_service import initialize_athlete_state, process_new_workout

pytestmark = pytest.mark.asyncio


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
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


async def _state_count(db, user_id: int) -> int:
    r = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(r.scalar_one())


async def _obs(db, user_id: int) -> list[BenchmarkObservation]:
    r = await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
    )
    return list(r.scalars().all())


# ── unit: the authority policy is fail-closed ─────────────────────────────────

def _row(**kw):
    from types import SimpleNamespace
    base = {
        "source": "benchmark_test", "evidence_type": "direct_measurement",
        "affects_capacity": True, "can_regress_capacity": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


async def test_policy_refuses_mismarked_workout_extraction():
    # Even if the flags say "capacity authoritative", a workout_extraction source is refused.
    mismarked = _row(source="workout_extraction")
    assert se.capacity_authoritative(mismarked) is False
    assert se.may_regress_capacity(mismarked, residual=-5.0) is False
    # A real benchmark test may regress.
    assert se.capacity_authoritative(_row()) is True
    assert se.may_regress_capacity(_row(), residual=-5.0) is True


async def test_extraction_gate_rejects_high_rep_low_effort():
    assert se.is_e1rm_informative(reps=5, rpe=8.0, rir=None) is True
    assert se.is_e1rm_informative(reps=12, rpe=9.0, rir=None) is False   # too many reps
    assert se.is_e1rm_informative(reps=5, rpe=6.0, rir=None) is False    # too easy
    # group_level (cloned quick-entry) needs a stricter bar
    assert se.is_e1rm_informative(reps=5, rpe=8.0, rir=None, effort_fidelity="group_level") is False
    assert se.is_e1rm_informative(reps=5, rpe=9.0, rir=None, effort_fidelity="group_level") is True


# ── boundary: workout_extraction never updates capacity ───────────────────────

async def test_workout_extraction_never_updates_capacity(async_db):
    user = await _user(async_db, "h1@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    before = await _state_count(async_db, user.id)

    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=120.0, source="workout_extraction",
        ),
    )
    # e1RM 120 sits below the baseline watermark → it raises no floor, so NO new
    # capacity state row. Its policy authority is upward_lower_bound (ADR-0058): it
    # may raise a floor when it exceeds the watermark, but can never regress capacity.
    assert len(await _obs(async_db, user.id)) == 1
    assert await _state_count(async_db, user.id) == before
    row = (await _obs(async_db, user.id))[0]
    assert row.capacity_effect == "upward_lower_bound"
    assert row.can_regress_capacity is False
    assert row.affects_capacity is True  # may raise a floor (upward only)


async def test_mismarked_workout_extraction_refused_at_boundary(async_db):
    user = await _user(async_db, "h2@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    before = await _state_count(async_db, user.id)

    # Caller tries to force capacity authority — the service must override + refuse.
    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=90.0, source="workout_extraction",
            affects_capacity=True, can_regress_capacity=True,
        ),
    )
    assert await _state_count(async_db, user.id) == before  # no capacity write
    row = (await _obs(async_db, user.id))[0]
    assert row.can_regress_capacity is False  # forced false regardless of caller


async def test_benchmark_test_still_updates_capacity_bidirectionally(async_db):
    user = await _user(async_db, "h3@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    before = await _state_count(async_db, user.id)

    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
        ),
    )
    # A protocol-grade benchmark DOES assimilate → a new capacity state row.
    assert await _state_count(async_db, user.id) == before + 1


# ── end-to-end: easy training does not lower max_strength ─────────────────────

async def _max_strength(db, user_id: int) -> float:
    from app.engine.state_bridge import unified_from_athlete_row
    from app.repositories.athlete_context_repository import AthleteContextRepository
    row = await AthleteContextRepository(db).get_latest_state(user_id)
    return float(unified_from_athlete_row(row).capacity_x.max_strength)


async def test_easy_training_does_not_lower_max_strength(async_db):
    user = await _user(async_db, "h4@test.com")
    await _seed(async_db)
    # Establish a real measured strength first (benchmark test).
    await initialize_athlete_state(async_db, user.id)
    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
        ),
    )
    strength_before = await _max_strength(async_db, user.id)

    # Log a hard-but-submaximal squat (informative: 5 reps @ RPE8) whose extrapolated
    # e1RM (~113) is well below the measured 150. Pre-hotfix this regressed capacity.
    log = WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=50.0,
        session_rpe=8.0,
        sets=[WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=100.0, reps=5, rpe=8.0)],
    )
    await process_new_workout(async_db, user.id, log)

    assert await _max_strength(async_db, user.id) >= strength_before  # never regressed
    # The extracted e1RM (~113) sits below the measured 150 watermark → it raised no
    # floor (history-only), and can never regress capacity (ADR-0055/0058).
    ext = [o for o in await _obs(async_db, user.id) if o.source == "workout_extraction"]
    assert len(ext) == 1
    assert ext[0].can_regress_capacity is False  # workout extraction never regresses
    assert ext[0].evidence_type == se.EV_ESTIMATED_FROM_TRAINING_SET  # below watermark → estimated


async def test_repair_restores_regressed_max_strength(async_db):
    """The conservative repair floors max_strength back to its historical watermark."""
    from app.engine.state_bridge import (
        athlete_state_kwargs_from_unified,
        unified_from_athlete_row,
    )
    from app.repositories.athlete_context_repository import AthleteContextRepository
    from app.scripts.repair_capacity_corruption import repair_with_db

    user = await _user(async_db, "h6@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    # Establish a high watermark via a real benchmark, then simulate the old bug: a
    # later corrupted row dropped max_strength below that watermark.
    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
        ),
    )
    high = await _max_strength(async_db, user.id)
    corrupted = unified_from_athlete_row(
        await AthleteContextRepository(async_db).get_latest_state(user.id)
    ).model_copy(deep=True)
    corrupted.capacity_x.max_strength = high - 40.0
    corrupted.timestamp = datetime.now(UTC).replace(tzinfo=None)
    async_db.add(AthleteState(user_id=user.id, **athlete_state_kwargs_from_unified(corrupted)))
    # A workout_extraction row marks the athlete as affected.
    async_db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=1, raw_value=110.0,
        source="workout_extraction", validity_status="quarantined",
    ))
    await async_db.commit()
    assert await _max_strength(async_db, user.id) < high  # corrupted

    n = await repair_with_db(async_db, apply=True)
    assert n == 1
    assert await _max_strength(async_db, user.id) == pytest.approx(high, abs=0.6)  # restored


async def test_high_rep_set_writes_no_extraction(async_db):
    user = await _user(async_db, "h5@test.com")
    await _seed(async_db)
    log = WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=40.0,
        session_rpe=7.0,
        sets=[WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=80.0, reps=12, rpe=7.0)],
    )
    await process_new_workout(async_db, user.id, log)
    assert [o for o in await _obs(async_db, user.id) if o.source == "workout_extraction"] == []
