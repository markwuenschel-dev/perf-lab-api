"""T9 — decline observability + the critical invariant metric (INT-02, ADR-0066).

The invariant `durable_strength_regressions_from_one_observation` must be 0 across
every scenario. requires_db — verified in CI + local docker PG.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service
from app.services import strength_decline_service as sds
from app.services.state_service import initialize_athlete_state

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
    d = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(d)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=d.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


async def _bench(db, user_id: int, raw: float, when: datetime) -> None:
    await benchmark_service.create_observation(
        db, user_id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=raw, source="benchmark_test",
            observed_at=when.replace(tzinfo=None),
        ),
    )


async def test_observability_counts_and_invariant_is_zero(async_db):
    user = await _user(async_db, "obs1@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    # 150 watermark → 138 candidate → 140 confirms (independent, >= interval).
    await _bench(async_db, user.id, 150.0, base)
    await _bench(async_db, user.id, 138.0, base + timedelta(days=10))
    await _bench(async_db, user.id, 140.0, base + timedelta(days=20))

    obs = await sds.decline_observability(async_db, user.id)
    assert obs.confirmed == 1
    assert obs.candidates_total == 1
    assert obs.confirmed_decline_magnitude > 0.0  # a real residual was recorded
    # The load-bearing guarantee.
    assert obs.durable_strength_regressions_from_one_observation == 0


async def test_invariant_zero_for_severe_and_active(async_db):
    # A severe route and an un-corroborated active candidate must also leave the
    # single-observation-regression invariant at zero.
    ua = await _user(async_db, "obs2@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, ua.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, ua.id, 150.0, base)
    await _bench(async_db, ua.id, 110.0, base + timedelta(days=10))  # severe → safety_routed

    obs = await sds.decline_observability(async_db, ua.id)
    assert obs.safety_routed == 1
    assert obs.confirmed == 0
    assert obs.durable_strength_regressions_from_one_observation == 0
