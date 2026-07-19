"""AUD-C24: WellnessTelemetrySnapshot — the immutable current-wellness boundary."""
import dataclasses
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import MissingGreenlet

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.user import User
from app.models.wellness import WellnessSample

pytestmark = pytest.mark.asyncio

_FIELDS = ("sleep_hours", "hrv_ms", "resting_hr", "soreness", "mood")


def _sample(**kw) -> WellnessSample:
    base = {"user_id": 1, "date": date(2026, 1, 1), "source": "manual"}
    return WellnessSample(**{**base, **kw})


def test_preserves_missing_vs_zero_semantics():
    # A legitimate 0.0 must not become missing; a missing None must not become 0.0.
    snap = WellnessTelemetrySnapshot.from_sample(
        _sample(sleep_hours=7.5, hrv_ms=None, resting_hr=0.0, soreness=0.0, mood=None)
    )
    assert snap.sleep_hours == 7.5
    assert snap.hrv_ms is None
    assert snap.resting_hr == 0.0  # a real zero, not missing
    assert snap.soreness == 0.0
    assert snap.mood is None  # missing, not 0.0


def test_carries_exactly_the_five_scalar_fields():
    fields = {f.name for f in dataclasses.fields(WellnessTelemetrySnapshot)}
    assert fields == set(_FIELDS)  # no identity/date/source fields leaked in
    snap = WellnessTelemetrySnapshot.from_sample(
        _sample(sleep_hours=7.0, hrv_ms=60.0, resting_hr=50.0, soreness=2.0, mood=8.0)
    )
    for f in _FIELDS:
        v = getattr(snap, f)
        assert v is None or isinstance(v, float)  # scalar, never an ORM object


def test_is_frozen():
    snap = WellnessTelemetrySnapshot.from_sample(_sample(soreness=2.0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.soreness = 9.0  # type: ignore[misc]


async def test_snapshot_survives_source_orm_expiration(async_db):
    user = User(email="c24_snap@test.com", hashed_password="x", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    sample = WellnessSample(
        user_id=user.id, date=date(2026, 1, 1), source="manual",
        sleep_hours=7.5, hrv_ms=60.0, resting_hr=50.0, soreness=0.0, mood=8.0,
    )
    async_db.add(sample)
    await async_db.commit()

    snap = WellnessTelemetrySnapshot.from_sample(sample)
    # Reproduce the shadow-cascade trigger: an early writer does DB work then rolls back,
    # which expires the source ORM instance (a no-op rollback would not expire it).
    await async_db.execute(text("SELECT 1"))
    await async_db.rollback()

    # The snapshot is a detached value copy, unaffected by the source ORM's expiration.
    assert snap.sleep_hours == 7.5 and snap.soreness == 0.0 and snap.mood == 8.0
    # ...whereas reading the now-expired ORM instance triggers an async lazy-load error.
    with pytest.raises(MissingGreenlet):
        _ = sample.sleep_hours
