"""The self-contained catalog seeders run against the current schema (PA-19).

app/scripts/* is excluded from coverage and the pyright gate, and the seeders construct
ORM rows directly — so a model/column rename breaks them silently until a human runs a
seed. These are the on-ramp for demo/eval data. This smoke-tests the two foundational,
self-contained catalog seeders end to end against the migrated test schema: they run
without raising, create rows, and are idempotent on a second run.

Scope: only the seeders whose data is embedded in code are covered. The dataset-backed
seeders (seed_demo_athletes, seed_fitbit_*, seed_openpl_strength, ...) read external
Kaggle/CSV files and are out of scope for a hermetic smoke test.

Requires a live DB (async_db). The seeders acquire their own session via
app.core.db.AsyncSessionLocal, so that is redirected to the test database.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.scripts import seed_benchmarks, seed_exercises

pytestmark = pytest.mark.asyncio


async def _count(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_catalog_seeders_run_clean_and_are_idempotent(
    async_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The seeders call AsyncSessionLocal() themselves; point it at the test database
    # (worker-specific) so they write where this test can read.
    test_factory = async_sessionmaker(async_db.bind, expire_on_commit=False)
    monkeypatch.setattr(seed_benchmarks, "AsyncSessionLocal", test_factory)
    monkeypatch.setattr(seed_exercises, "AsyncSessionLocal", test_factory)

    # Foundational order (as seed_all runs them): definitions before exercises, since
    # exercises carry an e1rm_benchmark_code referencing the benchmark catalog.
    await seed_benchmarks.seed()
    await seed_exercises.seed()
    await async_db.rollback()  # end any open snapshot so the counts see the seeders' commits

    benchmarks = await _count(async_db, BenchmarkDefinition)
    exercises = await _count(async_db, Exercise)
    assert benchmarks > 0, "seed_benchmarks must create benchmark definitions"
    assert exercises > 0, "seed_exercises must create exercises"

    # Idempotent: a second run raises nothing and inserts no duplicates.
    await seed_benchmarks.seed()
    await seed_exercises.seed()
    await async_db.rollback()
    assert await _count(async_db, BenchmarkDefinition) == benchmarks
    assert await _count(async_db, Exercise) == exercises
