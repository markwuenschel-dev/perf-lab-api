"""Transaction ownership of `benchmark_service.create_observation`.

**GATE 2 outcome: observations are durable. The data-loss hypothesis was FALSE.**

It was raised on this reading: `create_observation` does `db.add(obs)` + `await db.flush()`
(`benchmark_service.py:312-313`) and is called from `process_new_workout`
(`state_service.py:981`) AFTER that function's own commit (`state_service.py:963`), while
`get_db` (`app/core/db.py:43-46`) closes its session without committing. Since `flush()` is
not durability, the observation looked like it was being discarded on session close.

It is not. `create_observation` **commits at `benchmark_service.py:428`** — after resolving
capacity authority and applying weak-point feedback. The flush at :313 is mid-function, to
get `obs.id` for the downstream authority work; the commit lands ~115 lines later. The
original trace stopped reading at the flush and assumed the rest.

What is actually true, and worth pinning
----------------------------------------
`create_observation` **owns its own transaction**. It is not a leaky helper mid-flush; it is
a complete command that commits. The consequence is the real finding, and it is a design
property rather than a bug:

  A caller CANNOT compose it into a larger atomic unit. By the time it returns, the
  observation is committed. A later failure in the caller cannot roll it back.

So `process_new_workout` commits the workout at :963, then `create_observation` commits the
observation separately at :428. Two transactions, not one. That matches the post-commit
best-effort convention proven in W1-C2 (`ab858f6`) — but it means "observation exists <=>
its state consequences are consistent" is NOT guaranteed by the database, and any future
work wanting that atomicity has to move the boundary, not add a commit.

These tests read through a session that did NOT write the rows, so they measure durability
rather than session-local visibility.

requires_db - verified against real Postgres.
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

    The distinction is the point. The `async_db` fixture hands out one long-lived session,
    and `http_client` injects that same single session as `get_db` (`conftest.py:222-223`),
    so the suite normally observes writes through the session that made them. Opening and
    closing sessions explicitly here reproduces the production lifecycle instead.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _seed(factory) -> int:
    async with factory() as db:
        user = User(email="durability@test.com", hashed_password="x", is_active=True)
        db.add(user)
        db.add(
            BenchmarkDefinition(
                code="pl_e1rm_squat",
                name="Squat e1RM",
                domain="powerlifting",
                metric_type="load",
                unit="kg",
                better_direction="higher",
                observation_weight=1.0,
                standardization_rules={"floor": 40.0, "cap": 250.0},
            )
        )
        await db.commit()
        return user.id


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


async def test_observation_survives_the_request_session_closing(session_factory) -> None:
    """Regression guard on the commit at `benchmark_service.py:428`.

    The caller never commits — exactly as `get_db` never commits and as
    `state_service.py:981` calls this after its own :963 commit. The row must still be there
    when a different session looks.

    If this ever fails, that commit has been removed or moved behind a branch, and every
    workout-extracted e1RM observation is being silently discarded.
    """
    user_id = await _seed(session_factory)

    async with session_factory() as request_db:
        await benchmark_service.create_observation(request_db, user_id, _body())
        # No commit here. create_observation already did its own, internally.

    assert await _count_in_fresh_session(session_factory) == 1


async def test_caller_cannot_roll_back_a_created_observation(session_factory) -> None:
    """`create_observation` owns its transaction — this pins the consequence.

    An explicit rollback by the caller, immediately after the call, does not undo the row.
    That is what "the helper seized transaction ownership" means concretely: the observation
    is already durable before control returns, so it cannot participate in a larger atomic
    unit.

    This is not currently a defect — it matches the post-commit best-effort convention. It is
    pinned because any future work that needs "observation exists <=> state consequences are
    consistent" must move the transaction boundary, and this test is what will tell them the
    boundary is not where they assume.
    """
    user_id = await _seed(session_factory)

    async with session_factory() as request_db:
        await benchmark_service.create_observation(request_db, user_id, _body())
        await request_db.rollback()

    assert await _count_in_fresh_session(session_factory) == 1, (
        "A caller rollback undid the observation — create_observation no longer owns its "
        "transaction, and callers may now be composing it atomically. Re-verify :428."
    )


async def test_observation_survives_a_failing_kpi_recompute(session_factory, monkeypatch) -> None:
    """A post-commit KPI recompute that poisons the session must not 500 the committed write.

    ``create_observation`` commits the observation (:476), then best-effort recomputes the
    derived KPIs (:495) inside a try/except whose stated contract is "a KPI recompute failure
    must not fail the observation write." But the except only logged — it did not roll back —
    so a recompute that failed *after* poisoning the transaction left the session in a
    pending-rollback state, and the very next ``db.refresh(obs)`` (:503) raised
    ``PendingRollbackError``. The committed write then surfaced to the client as a 500.

    This pins the contract: a failed recompute is swallowed cleanly and the durable
    observation is still returned.
    """
    user_id = await _seed(session_factory)

    async def _poison(db: AsyncSession, _user_id: int) -> tuple[int, list[str]]:
        # Fail an in-flight statement so the transaction is marked for rollback — the generic
        # shape of a mid-recompute DB error (constraint / serialization / connection blip).
        await db.execute(sa.text("SELECT 1 / 0"))
        return (0, [])  # unreachable; the execute above raises

    from app.services import dashboard_service

    monkeypatch.setattr(dashboard_service, "recompute_derived_metrics", _poison)

    async with session_factory() as request_db:
        result = await benchmark_service.create_observation(request_db, user_id, _body())
        assert result.raw_value == 150.0

    # The observation committed at :476 and is durable despite the recompute failure.
    assert await _count_in_fresh_session(session_factory) == 1


async def test_flush_alone_is_not_durability(session_factory) -> None:
    """The mechanism the false alarm was built on, isolated so it stays understood.

    `flush()` really is not durability: a row added and flushed WITHOUT a commit vanishes
    when its session closes. That premise was sound. What was wrong was the claim that
    create_observation never commits — it does, at :428.

    Uses the ORM directly, deliberately bypassing create_observation, to test SQLAlchemy's
    behaviour under this app's actual session config rather than the service's.
    """
    user_id = await _seed(session_factory)
    definition_id = None
    async with session_factory() as db:
        definition_id = await db.scalar(select(BenchmarkDefinition.id))

    async with session_factory() as db:
        db.add(
            BenchmarkObservation(
                user_id=user_id,
                benchmark_definition_id=definition_id,
                raw_value=150.0,
                observed_at=datetime.now(UTC).replace(tzinfo=None),
                validity_status="valid",
                source="flush_only_probe",
            )
        )
        await db.flush()
        assert await db.scalar(select(func.count()).select_from(BenchmarkObservation)) == 1

    assert await _count_in_fresh_session(session_factory) == 0, (
        "A flushed-but-uncommitted row survived its session closing — the session config "
        "changed and uncommitted work is landing."
    )
