"""T10 — INT-02 invariant matrix + negative fixtures + P9 replay (ADR-0066).

Proves the load-bearing guarantees fail loudly for the wrong inputs and hold across
the historical regression class. requires_db.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.strength_decline_candidate import StrengthDeclineCandidate
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service, state_service
from app.services import strength_decline_service as sds
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db, *, code: str = "pl_e1rm_squat", rules: dict | None = None) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code=code,
    ))
    d = BenchmarkDefinition(
        code=code, name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules=rules,
    )
    db.add(d)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=d.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


async def _bench(db, user_id, raw, when, *, code="pl_e1rm_squat", source="benchmark_test"):
    await benchmark_service.create_observation(
        db, user_id,
        BenchmarkObservationCreate(
            benchmark_code=code, raw_value=raw, source=source,
            observed_at=when.replace(tzinfo=None),
        ),
    )


async def _candidates(db, user_id):
    r = await db.execute(
        select(StrengthDeclineCandidate).where(StrengthDeclineCandidate.user_id == user_id)
    )
    return list(r.scalars().all())


async def _max_strength(db, user_id) -> float:
    from app.engine.state_bridge import unified_from_athlete_row
    from app.repositories.athlete_context_repository import AthleteContextRepository
    row = await AthleteContextRepository(db).get_latest_state(user_id)
    return float(unified_from_athlete_row(row).capacity_x.max_strength)


# --- negative: workout-extraction cannot open a bidirectional decline candidate ----

async def test_workout_extraction_low_creates_no_decline_candidate(async_db):
    user = await _user(async_db, "inv1@test.com")
    await _seed(async_db, rules={"floor": 40.0, "cap": 250.0})
    await initialize_athlete_state(async_db, user.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, base)  # measured watermark
    # A workout-extracted low e1RM has upward_lower_bound authority only.
    await _bench(async_db, user.id, 138.0, base + timedelta(days=10), source="workout_extraction")
    assert await _candidates(async_db, user.id) == []


# --- negative: protocol-less ad-hoc cannot durably regress --------------------------

async def test_protocol_less_adhoc_low_opens_no_candidate(async_db):
    user = await _user(async_db, "inv2@test.com")
    await _seed(async_db, rules=None)  # no standardization_rules → protocol not_evaluated
    await initialize_athlete_state(async_db, user.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, base)
    strength_after = await _max_strength(async_db, user.id)
    await _bench(async_db, user.id, 138.0, base + timedelta(days=10))
    # No protocol grade → never bidirectional → no decline candidate, no regression.
    assert await _candidates(async_db, user.id) == []
    assert await _max_strength(async_db, user.id) >= strength_after - 0.01


# --- separation: historical watermark stays while the estimate declines -------------

async def test_confirmed_decline_keeps_watermark_but_lowers_estimate(async_db):
    user = await _user(async_db, "inv3@test.com")
    await _seed(async_db, rules={"floor": 40.0, "cap": 250.0})
    await initialize_athlete_state(async_db, user.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, base)
    strength_peak = await _max_strength(async_db, user.id)
    await _bench(async_db, user.id, 138.0, base + timedelta(days=10))
    await _bench(async_db, user.id, 140.0, base + timedelta(days=20))  # confirms

    # Historical best-validated e1RM is unchanged (150 is still the max valid raw)...
    watermark = await state_service.best_currently_validated_e1rm(async_db, user.id, "pl_e1rm_squat")
    assert watermark == 150.0
    # ...while the current latent estimate has declined (bounded).
    assert await _max_strength(async_db, user.id) < strength_peak


# --- P9 replay: a single low measured benchmark never durably regresses -------------

async def test_p9_replay_single_low_measured_benchmark(async_db):
    user = await _user(async_db, "inv4@test.com")
    await _seed(async_db, rules={"floor": 40.0, "cap": 250.0})
    await initialize_athlete_state(async_db, user.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, base)
    peak = await _max_strength(async_db, user.id)
    # A run of single low measured benchmarks (the P9 corruption class) — each a
    # different day but never independently corroborated within the retest interval.
    for i, raw in enumerate((132.0, 128.0, 135.0), start=1):
        await _bench(async_db, user.id, raw, base + timedelta(days=i))
        assert await _max_strength(async_db, user.id) >= peak - 0.01  # never regresses

    obs = await sds.decline_observability(async_db, user.id)
    assert obs.durable_strength_regressions_from_one_observation == 0
