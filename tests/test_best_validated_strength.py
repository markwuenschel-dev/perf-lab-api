"""T2 — `best_currently_validated_e1rm` semantics (INT-02, ADR-0066).

The historical-best watermark is separate from `capacity_x.max_strength` (current
latent). It is monotone on valid adds but MAY fall when the top observation is
quarantined/invalidated — and that fall is a *data correction*, never a manufactured
physiological decline.

requires_db — verified in CI (real Postgres). Not runnable in a DB-less env.
"""
from datetime import UTC, datetime

import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import User
from app.services import state_service

pytestmark = pytest.mark.asyncio


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _definition(db) -> BenchmarkDefinition:
    d = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def _obs(db, user_id: int, def_id: int, raw: float, *, validity: str = "valid") -> BenchmarkObservation:
    o = BenchmarkObservation(
        user_id=user_id, benchmark_definition_id=def_id, raw_value=raw,
        observed_at=datetime.now(UTC).replace(tzinfo=None), validity_status=validity,
        source="benchmark_test",
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def test_accessor_returns_max_valid(async_db):
    user = await _user(async_db, "bv1@test.com")
    d = await _definition(async_db)
    await _obs(async_db, user.id, d.id, 140.0)
    await _obs(async_db, user.id, d.id, 150.0)
    await _obs(async_db, user.id, d.id, 145.0)
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, "pl_e1rm_squat") == 150.0


async def test_unconfirmed_low_observation_does_not_lower_watermark(async_db):
    user = await _user(async_db, "bv2@test.com")
    d = await _definition(async_db)
    await _obs(async_db, user.id, d.id, 150.0)
    # A later *lower* valid observation does not pull the watermark down — it is the
    # max of valid observations, and a single low value is not a correction.
    await _obs(async_db, user.id, d.id, 138.0)
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, "pl_e1rm_squat") == 150.0


async def test_quarantine_top_drops_watermark_to_next_valid(async_db):
    """Data correction: quarantining the top e1RM lets the watermark fall — and this
    is NOT a manufactured decline transition (that distinction is enforced in T5)."""
    user = await _user(async_db, "bv3@test.com")
    d = await _definition(async_db)
    await _obs(async_db, user.id, d.id, 140.0)
    top = await _obs(async_db, user.id, d.id, 150.0)
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, "pl_e1rm_squat") == 150.0

    # Correct the record: quarantine the erroneous top observation.
    top.validity_status = "quarantined"
    top.quarantined_at = datetime.now(UTC).replace(tzinfo=None)
    top.quarantine_reason = "data_correction"
    await async_db.commit()

    # Watermark falls to the next valid observation; correction provenance stays auditable.
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, "pl_e1rm_squat") == 140.0
    assert top.quarantine_reason == "data_correction"
