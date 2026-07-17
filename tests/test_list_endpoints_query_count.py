"""Guard: the macrocycle/objective list assembly issues a bounded number of queries,
independent of how many rows are listed (PA-10).

Both ``to_read_schemas`` helpers used to assemble one row at a time, each firing its own
follow-up queries — so listing N rows cost ~2N round-trips (a classic N+1). These tests
count the SQL actually issued while assembling a K-row list and assert it stays a small
constant, and they check the batched values match what the per-row logic produced (the
objective test drives the trickier "latest observation per (user, definition)" path with
several observations).

Requires a live DB (async_db).
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import User
from app.schemas.macrocycle import MacrocycleCreate
from app.schemas.objective import ObjectiveCreate
from app.services import macrocycle_service, objective_service

pytestmark = pytest.mark.asyncio


@contextmanager
def _count_sql(session: AsyncSession):
    """Collect every SQL statement executed on the session's engine within the block."""
    sync_engine = session.bind.sync_engine  # type: ignore[union-attr]
    statements: list[str] = []

    def _on_exec(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _on_exec)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_exec)


async def _mk_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_list_macrocycles_query_count_is_bounded(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "qc_macro@test.com")
    for i in range(4):
        objective = await objective_service.create_objective(
            async_db, user.id, ObjectiveCreate(label=f"goal {i}", priority=1)
        )
        macro = await macrocycle_service.create_macrocycle(
            async_db, user.id, MacrocycleCreate(objective_id=objective.id, start_date=date.today())
        )
        assert macro is not None

    macrocycles = await macrocycle_service.list_macrocycles(async_db, user.id)
    assert len(macrocycles) == 4

    with _count_sql(async_db) as statements:
        reads = await macrocycle_service.to_read_schemas(async_db, macrocycles)

    # Two queries total (anchor objectives via IN, block counts via GROUP BY),
    # regardless of the 4 rows — the per-row path would have fired ~8.
    assert len(statements) <= 2, f"expected <=2 queries, got {len(statements)}:\n" + "\n".join(statements)
    assert len(reads) == 4
    assert all(r.objective_label.startswith("goal ") for r in reads)
    assert all(r.block_count == 0 for r in reads)  # no blocks created


async def test_list_objectives_query_count_is_bounded_and_picks_latest_observation(
    async_db: AsyncSession,
) -> None:
    user = await _mk_user(async_db, "qc_obj@test.com")

    definition = BenchmarkDefinition(
        code="qc_5k_time",
        name="5k Time Trial",
        domain="running",
        metric_type="time",
        unit="seconds",
        better_direction="lower",
    )
    async_db.add(definition)
    await async_db.commit()
    await async_db.refresh(definition)

    # Three benchmark-linked objectives sharing the definition, plus one free-text.
    for i in range(3):
        await objective_service.create_objective(
            async_db,
            user.id,
            ObjectiveCreate(label=f"sub-24 #{i}", benchmark_code=definition.code, target_value=1440.0),
        )
    await objective_service.create_objective(
        async_db,
        user.id,
        ObjectiveCreate(label="free text", target_date=date.today() + timedelta(days=10), priority=1),
    )

    # Two observations with distinct timestamps; the newer (1380.0) is the "latest".
    async_db.add_all([
        BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=definition.id,
            raw_value=1500.0, observed_at=datetime(2026, 1, 1, 12, 0, 0),
        ),
        BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=definition.id,
            raw_value=1380.0, observed_at=datetime(2026, 6, 1, 12, 0, 0),
        ),
    ])
    await async_db.commit()

    objectives = await objective_service.list_objectives(async_db, user.id)
    assert len(objectives) == 4

    with _count_sql(async_db) as statements:
        reads = await objective_service.to_read_schemas(async_db, objectives)

    # Two queries total (definitions via IN, observations via IN), regardless of the 4
    # rows — the per-row path would have fired up to 6 (2 per benchmark-linked objective).
    assert len(statements) <= 2, f"expected <=2 queries, got {len(statements)}:\n" + "\n".join(statements)

    by_label = {r.label: r for r in reads}
    for i in range(3):
        progress = by_label[f"sub-24 #{i}"].progress
        assert progress.current == 1380.0, "must pick the latest observation, not the older one"
        assert progress.direction == "lower"
        assert progress.pct == 100.0  # 1380 already beats the 1440 target (lower is better)
    assert by_label["free text"].progress.current is None
