"""GATE 2 — do benchmark observations actually survive the request?

`benchmark_service.create_observation` does `db.add(obs)` + `await db.flush()` and no
commit (`benchmark_service.py:312-313`). It is called from `process_new_workout`
(`state_service.py:981`) AFTER that function's own `await db.commit()`
(`state_service.py:963`) — so the observation is added to a transaction nothing commits
afterwards. `get_db` (`app/core/db.py:43-46`) is `async with AsyncSessionLocal() as
session: yield session` — no commit on exit. `flush()` sends changes within the current
transaction; it does not make them durable, and an uncommitted transaction is discarded
when the session closes.

Why no existing test catches this
---------------------------------
The `http_client` fixture overrides `get_db` with `yield async_db` (`conftest.py:222-223`)
— ONE session, held open by the fixture for the whole test. Production opens and closes a
session per request. So every DB test in this suite observes flushed-but-uncommitted rows
through the same session that created them, where they are visible right up until they
are not. The suite is structurally incapable of catching an uncommitted write.

That is exactly the false-green this file exists to break: these tests read through a
SEPARATE session opened after the writing one has closed.

requires_db — verified in CI (real Postgres). Not runnable in a DB-less env.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from conftest import _TRUNCATE_ALL, TEST_DATABASE_URL
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(_migrated_schema: None):
    """A session FACTORY, not a session.

    The distinction is the entire point. `async_db` hands out one long-lived session, which
    is what makes the suite blind here. Callers below open and close sessions explicitly so
    the production lifecycle is reproduced rather than simulated.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _seed(factory) -> tuple[int, int]:
    async with factory() as db:
        user = User(email="durability@test.com", hashed_password="x", is_active=True)
        db.add(user)
        definition = BenchmarkDefinition(
            code="pl_e1rm_squat",
            name="Squat e1RM",
            domain="powerlifting",
            metric_type="load",
            unit="kg",
            better_direction="higher",
            observation_weight=1.0,
            standardization_rules={"floor": 40.0, "cap": 250.0},
        )
        db.add(definition)
        await db.commit()
        return user.id, definition.id


async def _count_in_fresh_session(factory) -> int:
    """Count through a session that did NOT create the rows. The load-bearing move."""
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(BenchmarkObservation)) or 0


def _body() -> BenchmarkObservationCreate:
    return BenchmarkObservationCreate(
        benchmark_code="pl_e1rm_squat",
        raw_value=150.0,
        observed_at=datetime.now(UTC).replace(tzinfo=None),
        source="workout_extraction",
    )


async def test_flushed_observation_does_not_survive_session_close(session_factory) -> None:
    """THE GATE. Reproduces the production path exactly: create_observation's add+flush,
    then the session closes with no commit, exactly as `get_db` closes it.

    If this test FAILS (count == 1), transaction ownership exists somewhere outside the
    traced functions — find it, document it, and keep this test as the regression guard.

    If this test PASSES (count == 0), every e1RM observation extracted from a workout has
    been silently discarded, and the fix belongs at the top-level command boundary — NOT a
    commit() inside create_observation, which would seize transaction ownership and could
    leave the observation durable while the state update failed.
    """
    user_id, _ = await _seed(session_factory)

    async with session_factory() as request_db:
        await benchmark_service.create_observation(request_db, user_id, _body())
        # No commit — precisely what state_service.py:981 does after its :963 commit.
        assert await request_db.scalar(select(func.count()).select_from(BenchmarkObservation)) == 1

    assert await _count_in_fresh_session(session_factory) == 0, (
        "Observation SURVIVED without a commit — transaction ownership exists outside the "
        "traced path. Document the owner and keep this test as its regression guard."
    )


async def test_committed_observation_does_survive(session_factory) -> None:
    """Control. Proves the test above measures the commit, not a broken fixture.

    Without this, a green 'does not survive' could equally mean the fixture never wrote
    anything at all.
    """
    user_id, _ = await _seed(session_factory)

    async with session_factory() as request_db:
        await benchmark_service.create_observation(request_db, user_id, _body())
        await request_db.commit()

    assert await _count_in_fresh_session(session_factory) == 1


async def test_the_shared_session_fixture_cannot_detect_this(session_factory) -> None:
    """Documents the false-green mechanism itself, so it cannot quietly return.

    Same session: the row is visible. Different session: it is gone. Every DB test in this
    suite is on the first branch — which is why this has stayed invisible.
    """
    user_id, _ = await _seed(session_factory)

    async with session_factory() as request_db:
        await benchmark_service.create_observation(request_db, user_id, _body())
        same_session_count = await request_db.scalar(
            select(func.count()).select_from(BenchmarkObservation)
        )

    fresh_count = await _count_in_fresh_session(session_factory)

    assert same_session_count == 1, "flush is visible to its own session"
    assert fresh_count == 0, "and to nobody else"
    assert same_session_count != fresh_count, (
        "This inequality IS the bug class: a test asserting through the writing session "
        "passes while the row never lands."
    )
