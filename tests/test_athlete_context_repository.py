"""Tests for AthleteContextRepository — the athlete-context persistence seam.

Exercised through the public interface against the real test DB (async_db
fixture). See CONTEXT.md for the boundary contract: the repository owns
persistence mechanics only, and returns ORM rows (no domain mapping).
"""
from datetime import datetime, timedelta

from app.models.athlete_state import AthleteState
from app.models.user import User
from app.repositories.athlete_context_repository import AthleteContextRepository


def _state(user_id: int, ts: datetime, c_met: float = 50.0) -> AthleteState:
    return AthleteState(
        user_id=user_id,
        timestamp=ts,
        c_met_aerobic=c_met,
        c_nm_force=50.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
    )


async def _make_user(async_db, email: str) -> User:
    user = User(email=email, hashed_password="x")
    async_db.add(user)
    await async_db.flush()
    return user


async def test_get_latest_state_returns_most_recent(async_db):
    user = await _make_user(async_db, "latest@test.io")
    base = datetime(2026, 1, 1, 12, 0, 0)
    async_db.add_all(
        [
            _state(user.id, base, c_met=10.0),
            _state(user.id, base + timedelta(days=2), c_met=30.0),  # newest
            _state(user.id, base + timedelta(days=1), c_met=20.0),
        ]
    )
    await async_db.flush()

    repo = AthleteContextRepository(async_db)
    latest = await repo.get_latest_state(user.id)

    assert latest is not None
    assert latest.c_met_aerobic == 30.0


async def test_get_latest_state_isolates_by_user(async_db):
    a = await _make_user(async_db, "a@test.io")
    b = await _make_user(async_db, "b@test.io")
    base = datetime(2026, 1, 1, 12, 0, 0)
    async_db.add(_state(a.id, base, c_met=11.0))
    # b has a strictly newer state — it must not leak into a's result.
    async_db.add(_state(b.id, base + timedelta(days=5), c_met=99.0))
    await async_db.flush()

    repo = AthleteContextRepository(async_db)
    latest = await repo.get_latest_state(a.id)

    assert latest is not None
    assert latest.c_met_aerobic == 11.0


async def test_get_latest_state_returns_none_when_no_state(async_db):
    user = await _make_user(async_db, "empty@test.io")
    repo = AthleteContextRepository(async_db)
    assert await repo.get_latest_state(user.id) is None
