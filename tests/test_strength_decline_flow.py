"""T5 — decline assessment (pure) + ingestion intercept (DB) (INT-02, ADR-0066).

Pure tests run anywhere; DB tests require Postgres (CI + local docker PG).
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.logic import strength_decline_policy as policy
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.strength_decline_candidate import (
    STATUS_ACTIVE,
    STATUS_CONFIRMED,
    STATUS_DISMISSED,
    StrengthDeclineCandidate,
)
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service, strength_decline_service
from app.services.state_service import initialize_athlete_state

# --------------------------------------------------------------------------- #
# Pure assessment (no DB)
# --------------------------------------------------------------------------- #

def test_assess_small_drop_immaterial():
    a = strength_decline_service.assess_decline(
        prior_mean=150.0, observed_value=149.0, error=None, mean_fatigue=0.0
    )
    assert a.classification == policy.STABLE
    assert a.is_material is False


def test_assess_large_drop_material():
    a = strength_decline_service.assess_decline(
        prior_mean=150.0, observed_value=138.0, error=None, mean_fatigue=0.0
    )
    assert a.classification == policy.DECLINE_CANDIDATE
    assert a.is_material is True


def test_assess_huge_drop_severe():
    a = strength_decline_service.assess_decline(
        prior_mean=150.0, observed_value=125.0, error=None, mean_fatigue=0.0
    )
    assert a.classification == policy.SEVERE_DECLINE


def test_fatigue_inflates_threshold_so_borderline_drop_is_immaterial_when_fatigued():
    # A 7kg drop is material when rested but noise when fatigued (obs treated noisier).
    rested = strength_decline_service.assess_decline(
        prior_mean=150.0, observed_value=143.0, error=None, mean_fatigue=0.0
    )
    fatigued = strength_decline_service.assess_decline(
        prior_mean=150.0, observed_value=143.0, error=None, mean_fatigue=1.0
    )
    assert rested.is_material is True
    assert fatigued.is_material is False
    assert fatigued.threshold.threshold > rested.threshold.threshold


# --------------------------------------------------------------------------- #
# Ingestion intercept (DB)
# --------------------------------------------------------------------------- #


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


async def _max_strength(db, user_id: int) -> float:
    from app.engine.state_bridge import unified_from_athlete_row
    from app.repositories.athlete_context_repository import AthleteContextRepository
    row = await AthleteContextRepository(db).get_latest_state(user_id)
    return float(unified_from_athlete_row(row).capacity_x.max_strength)


async def _candidates(db, user_id: int) -> list[StrengthDeclineCandidate]:
    from sqlalchemy import select
    r = await db.execute(
        select(StrengthDeclineCandidate).where(StrengthDeclineCandidate.user_id == user_id)
    )
    return list(r.scalars().all())


async def _benchmark(db, user_id: int, raw: float, observed_at: datetime):
    return await benchmark_service.create_observation(
        db, user_id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=raw, source="benchmark_test",
            observed_at=observed_at.replace(tzinfo=None),
        ),
    )


# A realistic timeline: the seed row is stamped at init (~now); benchmarks follow
# chronologically after it so `get_latest_state` reflects the most recent benchmark.
def _timeline() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_material_low_retest_opens_candidate_without_regressing(async_db):
    user = await _user(async_db, "d1@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)

    base = _timeline()
    await _benchmark(async_db, user.id, 150.0, base)          # establishes watermark 150
    strength_after_150 = await _max_strength(async_db, user.id)

    await _benchmark(async_db, user.id, 138.0, base + timedelta(days=1))  # material drop

    # No durable regression: canonical max_strength is held at its prior.
    assert await _max_strength(async_db, user.id) == pytest.approx(strength_after_150, abs=0.01)
    # A decline candidate is opened.
    cands = await _candidates(async_db, user.id)
    assert len(cands) == 1
    assert cands[0].status == STATUS_ACTIVE
    assert cands[0].prior_mean == pytest.approx(150.0)
    assert cands[0].observed_value == pytest.approx(138.0)


@pytest.mark.asyncio
async def test_first_benchmark_has_no_prior_and_applies_normally(async_db):
    user = await _user(async_db, "d2@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    await _benchmark(async_db, user.id, 150.0, _timeline())  # first ever → no prior watermark
    assert await _candidates(async_db, user.id) == []


@pytest.mark.asyncio
async def test_immaterial_low_retest_holds_but_opens_no_candidate(async_db):
    user = await _user(async_db, "d3@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    base = _timeline()
    await _benchmark(async_db, user.id, 150.0, base)
    strength_after_150 = await _max_strength(async_db, user.id)
    await _benchmark(async_db, user.id, 149.0, base + timedelta(days=1))  # inside error band
    # No durable regression (the estimate may still converge upward — 149 is above the
    # current latent belief — but it must never be pulled DOWN) and no candidate.
    assert await _max_strength(async_db, user.id) >= strength_after_150 - 0.01
    assert await _candidates(async_db, user.id) == []


@pytest.mark.asyncio
async def test_independent_corroboration_confirms_bounded_decline(async_db):
    user = await _user(async_db, "d4@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    base = _timeline()
    await _benchmark(async_db, user.id, 150.0, base)                       # watermark
    strength_after_150 = await _max_strength(async_db, user.id)
    await _benchmark(async_db, user.id, 138.0, base + timedelta(days=10))  # trigger candidate
    await _benchmark(async_db, user.id, 140.0, base + timedelta(days=20))  # confirm (>=7d, new day)

    cands = await _candidates(async_db, user.id)
    assert len(cands) == 1
    assert cands[0].status == STATUS_CONFIRMED
    assert cands[0].applied_posterior_mean is not None
    assert cands[0].confirmation_observation_id is not None

    strength_after = await _max_strength(async_db, user.id)
    observed_axis = (140.0 - 40.0) / 210.0 * 100.0  # ≈ 47.6
    # Bounded reduction: below the prior, but NOT overwritten to the low observation.
    assert strength_after < strength_after_150
    assert strength_after > observed_axis


@pytest.mark.asyncio
async def test_re_demonstration_dismisses_candidate(async_db):
    user = await _user(async_db, "d5@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    base = _timeline()
    await _benchmark(async_db, user.id, 150.0, base)
    await _benchmark(async_db, user.id, 138.0, base + timedelta(days=10))  # candidate
    await _benchmark(async_db, user.id, 151.0, base + timedelta(days=20))  # re-demonstration

    cands = await _candidates(async_db, user.id)
    assert len(cands) == 1
    assert cands[0].status == STATUS_DISMISSED


@pytest.mark.asyncio
async def test_second_low_too_soon_does_not_confirm(async_db):
    user = await _user(async_db, "d6@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    base = _timeline()
    await _benchmark(async_db, user.id, 150.0, base)
    await _benchmark(async_db, user.id, 138.0, base + timedelta(days=10))  # candidate
    strength_before = await _max_strength(async_db, user.id)
    await _benchmark(async_db, user.id, 139.0, base + timedelta(days=12))  # only 2 days later

    cands = await _candidates(async_db, user.id)
    assert len(cands) == 1
    assert cands[0].status == STATUS_ACTIVE  # not confirmed — insufficient separation
    # Held: no durable regression from the un-corroborated second low.
    assert await _max_strength(async_db, user.id) >= strength_before - 0.01
