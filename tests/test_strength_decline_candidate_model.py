"""T3 — strength_decline_candidates model + idempotency constraint (INT-02, ADR-0066).

requires_db — verified in CI (real Postgres). Not runnable in a DB-less env.
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.logic import strength_decline_policy as policy
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.strength_decline_candidate import (
    STATUS_ACTIVE,
    StrengthDeclineCandidate,
)
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _fixtures(db) -> tuple[int, int, int]:
    user = User(email="dc@test.com", hashed_password="x", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    d = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher", observation_weight=1.0,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    obs = BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=d.id, raw_value=138.0,
        observed_at=datetime.now(UTC).replace(tzinfo=None), source="benchmark_test",
    )
    db.add(obs)
    await db.commit()
    await db.refresh(obs)
    return user.id, d.id, obs.id


def _candidate(user_id: int, def_id: int, obs_id: int) -> StrengthDeclineCandidate:
    return StrengthDeclineCandidate(
        user_id=user_id, capacity_axis="max_strength", benchmark_definition_id=def_id,
        benchmark_code="pl_e1rm_squat", trigger_observation_id=obs_id,
        prior_mean=150.0, prior_variance=1.0, observed_value=138.0, observation_variance=4.0,
        measurement_error_threshold=6.3, normalized_residual=12.0,
        threshold_source=policy.ME_SOURCE_MDC, status=STATUS_ACTIVE,
        authority_policy_version="authority_policy_v1",
        decline_policy_version=policy.POLICY_VERSION,
    )


async def test_candidate_round_trips(async_db):
    user_id, def_id, obs_id = await _fixtures(async_db)
    async_db.add(_candidate(user_id, def_id, obs_id))
    await async_db.commit()
    row = (await async_db.get(StrengthDeclineCandidate, 1))
    assert row is not None
    assert row.status == STATUS_ACTIVE
    assert row.observed_value == 138.0
    assert row.decline_policy_version == policy.POLICY_VERSION


async def test_idempotency_constraint_rejects_replay(async_db):
    """Replay of the same (trigger_observation, axis, policy) cannot open a parallel candidate."""
    user_id, def_id, obs_id = await _fixtures(async_db)
    async_db.add(_candidate(user_id, def_id, obs_id))
    await async_db.commit()
    async_db.add(_candidate(user_id, def_id, obs_id))  # same trigger obs + axis + policy
    with pytest.raises(IntegrityError):
        await async_db.commit()
