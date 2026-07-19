"""AUD-C24-3: AthleteContextRepository.list_wellness_history — immutable projections.

Locks the contract personalization's feature-builder now depends on: ascending + deterministic
ordering, athlete scoping, empty -> [], through_date/limit, and that returned values are
projections (not ORM entities) that survive the session lifecycle.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.models.user import User
from app.models.wellness import WellnessSample
from app.repositories.athlete_context_repository import (
    AthleteContextRepository,
    WellnessHistoryPoint,
)

pytestmark = pytest.mark.asyncio

_D0 = date(2026, 1, 1)


async def _mk_user(db, email) -> int:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u.id


async def _seed(db, user_id, days, *, source="manual"):
    for i in days:
        db.add(
            WellnessSample(
                user_id=user_id, date=_D0 + timedelta(days=i), source=source,
                sleep_hours=7.0 + i, hrv_ms=60.0 - i, resting_hr=50.0, soreness=1.0, mood=8.0,
            )
        )
    await db.commit()


async def test_ordered_ascending_scoped_and_projected(async_db):
    a = await _mk_user(async_db, "wh_a@test.com")
    b = await _mk_user(async_db, "wh_b@test.com")
    await _seed(async_db, a, [2, 0, 1])  # inserted out of order
    await _seed(async_db, b, [0])

    points = await AthleteContextRepository(async_db).list_wellness_history(a)

    assert [p.recorded_date for p in points] == [_D0, _D0 + timedelta(days=1), _D0 + timedelta(days=2)]
    assert all(isinstance(p, WellnessHistoryPoint) for p in points)  # projection, not ORM
    assert not any(isinstance(p, WellnessSample) for p in points)
    assert points[0].sleep_hours == 7.0 and points[0].hrv_ms == 60.0
    # scoped to the requested athlete
    assert len(await AthleteContextRepository(async_db).list_wellness_history(b)) == 1


async def test_empty_history_returns_empty(async_db):
    uid = await _mk_user(async_db, "wh_empty@test.com")
    assert await AthleteContextRepository(async_db).list_wellness_history(uid) == []


async def test_through_date_and_limit(async_db):
    uid = await _mk_user(async_db, "wh_filter@test.com")
    await _seed(async_db, uid, [0, 1, 2, 3, 4])
    repo = AthleteContextRepository(async_db)

    through = await repo.list_wellness_history(uid, through_date=_D0 + timedelta(days=2))
    assert [p.recorded_date for p in through] == [_D0 + timedelta(days=i) for i in (0, 1, 2)]

    limited = await repo.list_wellness_history(uid, limit=2)
    assert [p.recorded_date for p in limited] == [_D0, _D0 + timedelta(days=1)]  # oldest first


async def test_projections_are_independent_of_session_lifecycle(async_db):
    uid = await _mk_user(async_db, "wh_detach@test.com")
    await _seed(async_db, uid, [0])
    points = await AthleteContextRepository(async_db).list_wellness_history(uid)

    # An early shadow's rollback expires ORM rows; the projections are value copies, unaffected.
    await async_db.execute(text("SELECT 1"))
    await async_db.rollback()
    assert points[0].sleep_hours == 7.0 and points[0].recorded_date == _D0
