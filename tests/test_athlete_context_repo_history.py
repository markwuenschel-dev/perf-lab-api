"""AUD-C15: repository history reads + the ``state_service.load_recent_states`` loader.

Locks the contracts the ``/v1/state-history`` and ``/v1/workouts`` routes now delegate to,
behind the repository seam instead of inline in the route (CONTEXT.md): ordering, limit,
athlete scoping, and empty-result behavior.
"""
from datetime import datetime, timedelta

import pytest

from app.domain.vectors import FatigueState
from app.engine.simulate import baseline_state
from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.models.athlete_state import AthleteState
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.services import state_service

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _mk_user(db, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _state_row(user_id: int, when: datetime) -> AthleteState:
    s = baseline_state(when=when)
    for k in FatigueState.KEYS:
        setattr(s.fatigue_f, k, 10.0)
    return AthleteState(user_id=user_id, **athlete_state_kwargs_from_unified(s))


def _workout(user_id: int, when: datetime) -> WorkoutLog:
    return WorkoutLog(
        user_id=user_id,
        logged_at=when,
        session_timestamp=when,
        modality="Mixed",
        duration_minutes=30.0,
        session_rpe=5.0,
    )


async def _seed_states(db, user_id: int, days: list[int]) -> None:
    for d in days:
        db.add(_state_row(user_id, _T0 + timedelta(days=d)))
    await db.commit()


async def test_list_recent_states_newest_first_and_limited(async_db):
    user = await _mk_user(async_db, "hist_states@test.com")
    await _seed_states(async_db, user.id, [0, 1, 2, 3, 4])
    recent = await AthleteContextRepository(async_db).list_recent_states(user.id, limit=3)

    ts = [r.timestamp for r in recent]
    assert len(ts) == 3
    assert ts == sorted(ts, reverse=True)  # newest first
    assert ts[0] == _T0 + timedelta(days=4)


async def test_list_states_ascending_returns_full_history_oldest_first(async_db):
    user = await _mk_user(async_db, "hist_asc@test.com")
    await _seed_states(async_db, user.id, [2, 0, 1])  # insert out of order
    rows = await AthleteContextRepository(async_db).list_states_ascending(user.id)

    ts = [r.timestamp for r in rows]
    assert len(ts) == 3
    assert ts == sorted(ts)  # oldest first


async def test_load_recent_states_returns_oldest_to_newest_vectors(async_db):
    user = await _mk_user(async_db, "hist_loader@test.com")
    await _seed_states(async_db, user.id, [0, 1, 2, 3])
    vectors = await state_service.load_recent_states(async_db, user.id, limit=2)

    # limit=2 -> the two most recent rows, returned oldest->newest (chart order)
    assert len(vectors) == 2
    assert vectors[0].timestamp < vectors[1].timestamp
    assert vectors[1].timestamp == _T0 + timedelta(days=3)


async def test_history_reads_are_user_scoped_and_empty_is_empty(async_db):
    a = await _mk_user(async_db, "hist_a@test.com")
    b = await _mk_user(async_db, "hist_b@test.com")
    await _seed_states(async_db, a.id, [0, 1])
    repo = AthleteContextRepository(async_db)

    assert len(await repo.list_recent_states(b.id, limit=10)) == 0
    assert len(await repo.list_states_ascending(b.id)) == 0
    assert await state_service.load_recent_states(async_db, b.id, 10) == []


async def test_list_recent_workouts_newest_first_limited_and_scoped(async_db):
    a = await _mk_user(async_db, "hist_wko_a@test.com")
    b = await _mk_user(async_db, "hist_wko_b@test.com")
    for d in [0, 1, 2]:
        async_db.add(_workout(a.id, _T0 + timedelta(days=d)))
    async_db.add(_workout(b.id, _T0))
    await async_db.commit()
    repo = AthleteContextRepository(async_db)

    recent = await repo.list_recent_workouts(a.id, limit=2)
    logged = [w.logged_at for w in recent]
    assert len(logged) == 2
    assert logged == sorted(logged, reverse=True)  # newest first
    assert logged[0] == _T0 + timedelta(days=2)
    assert len(await repo.list_recent_workouts(b.id, limit=10)) == 1  # scoped
