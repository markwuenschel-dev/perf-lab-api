"""
Pytest configuration and shared fixtures.

DB fixtures use a dedicated test database (perflab_test). If the database server
is genuinely unreachable the integration tests skip gracefully; any *other*
failure (migration error, event-loop misuse, schema drift) fails loudly rather
than masquerading as a skip.

Strategy:
- Tables are created via Alembic migrations (not Base.metadata.create_all),
  so tests exercise the same schema production uses and catch migration drift.
- Migrations run **once per session** against a freshly-created ``public`` schema;
  per-test isolation is a TRUNCATE of every table instead of a schema rebuild.
- Migrations run on the *test* connection via ``config.attributes["connection"]``
  (see ``alembic/env.py``), so there is no dependency on the app's configured
  DATABASE_URL.

Why once-per-session rather than once-per-test: the schema rebuild used to be
function-scoped, so all 35 migrations replayed from a000 for *every* DB-backed
test. At 169 such tests that is ~5,900 migration runs per suite, and it dominated
the wall clock — 169 DB tests took 248s (1.47s each) against 38s for the other 848.
The migrations still run, against a genuinely fresh schema, so drift is still
caught; they just run once. TRUNCATE ... RESTART IDENTITY CASCADE gives the same
empty-database guarantee per test at a fraction of the cost.

Isolation caveat: TRUNCATE clears *data*, not DDL. A test that alters the schema
mid-run would leak into its neighbours, where the old drop-and-recreate would have
absorbed it. No test does this today; if one ever needs to, it should own its own
schema explicitly rather than relying on the fixture to rebuild.
"""
import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# HARD COST KILL — must run BEFORE any app import (Settings reads .env).
# A developer .env with LITELLM_VIRTUAL_KEY / OPENAI_API_KEY must never bill
# providers during pytest. Process env overrides pydantic env_file values.
# ---------------------------------------------------------------------------
for _cost_env in (
    "LITELLM_VIRTUAL_KEY",
    "LITELLM_BASE_URL",
    "LITELLM_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
):
    os.environ[_cost_env] = ""
os.environ["LLG_HERMETIC"] = "1"
os.environ["PERF_LAB_HERMETIC_TESTS"] = "1"

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Import models so their metadata/relationships are registered for migrations.
import app.models  # noqa: F401
from alembic import command
from app.core.db import get_db


@pytest.fixture(autouse=True)
def _hermetic_no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep provider/gateway credentials empty for every test (cost leak guard)."""
    for key in (
        "LITELLM_VIRTUAL_KEY",
        "LITELLM_BASE_URL",
        "LITELLM_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("LLG_HERMETIC", "1")
    monkeypatch.setenv("PERF_LAB_HERMETIC_TESTS", "1")


@contextmanager
def assert_does_not_raise() -> Iterator[None]:
    """Assert the wrapped block raises nothing — the explicit form of a "must not raise" test.

    A positive-fixture / does-not-fail-closed test (a guard that must *permit* a valid input)
    otherwise has no assertion: it passes on the mere absence of an exception, which reads as
    an empty test and is flagged by test_all_tests_assert_something.py. Wrapping the act here
    states the contract and turns an incidental raise into a clear failure instead of a bare
    traceback.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — the assertion is "no exception of any kind"
        pytest.fail(f"expected no exception, but {type(exc).__name__} was raised: {exc}")


_BASE_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)


def _worker_database_url() -> str:
    """Give each xdist worker its own database.

    Per-test isolation is a TRUNCATE of every table, which is process-global — under
    ``-n auto`` one worker would wipe another's rows mid-test. Sharding by
    ``PYTEST_XDIST_WORKER`` (``gw0``, ``gw1``, …) keeps the workers from colliding.
    Serial runs keep the plain database name, so nothing changes without xdist.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return _BASE_DATABASE_URL
    url = make_url(_BASE_DATABASE_URL)
    # render_as_string(hide_password=False), not str(): str(URL) masks the password
    # as a literal "***", which produces a URL that looks right and cannot connect.
    return url.set(database=f"{url.database}_{worker}").render_as_string(hide_password=False)


TEST_DATABASE_URL = _worker_database_url()

# Connection-level failures that legitimately mean "no database available" — the
# only conditions under which an integration test is allowed to skip. Anything
# else (e.g. a broken migration) must surface as a real error.
_DB_UNAVAILABLE = (OSError, ConnectionError, OperationalError, InterfaceError)


def pytest_addoption(parser: pytest.Parser) -> None:
    """``--golden-update`` rewrites characterization corpora instead of asserting on them.

    Used by tests/test_prescription_goldens.py: a prescribed-work change is either intended —
    rewrite the golden, read the diff, put the before/after in the PR — or it is the defect
    the corpus exists to catch.
    """
    parser.addoption(
        "--golden-update",
        action="store_true",
        default=False,
        help="rewrite golden/characterization corpora rather than comparing against them",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_db: test needs a live PostgreSQL connection "
        "(auto-applied to any test using the async_db or http_client fixture)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Structurally mark every DB-backed test.

    A test "needs a database" iff it requests the ``async_db`` or ``http_client``
    fixture. Deriving the marker from fixture usage keeps ``-m requires_db`` /
    ``-m "not requires_db"`` reliable without hand-marking ~90 files (which drifts).
    """
    for item in items:
        fixturenames = getattr(item, "fixturenames", ())
        if "async_db" in fixturenames or "http_client" in fixturenames:
            item.add_marker(pytest.mark.requires_db)


def _run_upgrade(connection: Connection) -> None:
    """Run Alembic migrations to head on an existing (sync-facing) connection."""
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


# Empties every table the migrations created, without touching DDL. alembic_version is
# deliberately preserved — truncating it would strand the schema at an unknown revision
# for the rest of the session.
#
# Only the tables that actually hold rows are truncated, in one statement. Two reasons,
# both measured on this schema (33 tables, 122 indexes, 33 sequences):
#
# 1. Locks. TRUNCATE takes ACCESS EXCLUSIVE on each named table *and* its indexes and
#    toast relations — ~250 lock entries for the full set — held until the transaction
#    ends. Those come from one server-wide lock table sized
#    max_locks_per_transaction * max_connections (64 * 100 = 6400 by default), so under
#    ``-n auto`` on a many-core box the workers collectively overflow it and Postgres
#    raises "out of shared memory". A typical test dirties a handful of tables, so naming
#    only those keeps a worker's peak in the tens.
# 2. Speed. TRUNCATE's cost is per *statement*, not per table — it recreates relation
#    files. Measured here: ~665 ms whether it names 33 tables or one. So the win is
#    skipping the statement entirely when nothing is dirty, not naming fewer tables.
#    (Chunking across several statements is therefore the wrong shape: it multiplies the
#    expensive part. An earlier attempt at that made the suite 3.5x slower.)
#
# Semantics are unchanged from truncating everything: an untouched table is already empty
# and its sequence already at 1, because whatever last emptied it did so with RESTART
# IDENTITY. CASCADE may pull in a table the probe called empty; harmless.
_TRUNCATE_CHUNK_SIZE = 20

# One round trip (~1.7 ms), no dynamic SQL from Python: query_to_xml runs a LIMIT 1 probe
# per table server-side, so an empty table costs an empty scan rather than a full count.
_NONEMPTY_PUBLIC_TABLES = sa.text(
    """
    SELECT format('%I.%I', n.nspname, c.relname) AS qname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND c.relname <> 'alembic_version'
       AND (xpath('/row/c/text()',
                  query_to_xml(
                      format('SELECT count(*) AS c FROM (SELECT 1 FROM %I.%I LIMIT 1) s',
                             n.nspname, c.relname),
                      false, true, '')))[1]::text::int > 0
     ORDER BY 1
    """
)


async def _truncate_all(engine) -> None:
    """Empty every table that holds rows, resetting their identities."""
    async with engine.connect() as conn:
        names = [row[0] for row in (await conn.execute(_NONEMPTY_PUBLIC_TABLES)).fetchall()]
        for start in range(0, len(names), _TRUNCATE_CHUNK_SIZE):
            chunk = ", ".join(names[start : start + _TRUNCATE_CHUNK_SIZE])
            await conn.execute(sa.text(f"TRUNCATE TABLE {chunk} RESTART IDENTITY CASCADE"))
            await conn.commit()


async def _ensure_database_exists() -> None:
    """Create this worker's database if it does not exist yet.

    Only xdist workers need this — CI's Postgres service creates the base
    ``perflab_test`` but knows nothing about ``perflab_test_gw0`` etc. CREATE
    DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    url = make_url(TEST_DATABASE_URL)
    if url.database == make_url(_BASE_DATABASE_URL).database:
        return  # serial run: the base database is provisioned externally
    admin = create_async_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                await conn.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await admin.dispose()


# Any stable 64-bit int works; every worker must use the same one.
_SCHEMA_BUILD_LOCK_KEY = 0x7E571AB1


async def _build_schema() -> None:
    """Drop+recreate ``public`` and migrate it to head. Runs once per session.

    Serialized across xdist workers by a Postgres advisory lock. ``DROP SCHEMA CASCADE``
    and the Alembic upgrade each hold ACCESS EXCLUSIVE on every object in the schema for
    the length of their transaction, and *every* worker runs this at startup at the same
    moment. The lock table is server-wide (see ``_TRUNCATE_CHUNK_SIZE``), so N workers
    multiply the demand and overflow it — which surfaces as "out of shared memory" from
    ``DROP SCHEMA``, failing the session-scoped fixture and erroring every test in that
    worker. The gate makes the peak one worker's worth instead of N.

    The lock is held on a separate AUTOCOMMIT connection to the ``postgres`` database and
    is released when that session closes, so ``dispose()`` in the ``finally`` frees it
    even if the build raises.
    """
    await _ensure_database_exists()
    gate_url = (
        make_url(TEST_DATABASE_URL)
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )
    gate_engine = create_async_engine(
        gate_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with gate_engine.connect() as gate:
            await gate.execute(
                sa.text("SELECT pg_advisory_lock(:key)"),
                {"key": _SCHEMA_BUILD_LOCK_KEY},
            )
            engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
            try:
                async with engine.begin() as conn:
                    await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                    await conn.execute(sa.text("CREATE SCHEMA public"))
                    await conn.execute(sa.text("GRANT ALL ON SCHEMA public TO PUBLIC"))
                async with engine.connect() as conn:
                    await conn.run_sync(_run_upgrade)
            finally:
                await engine.dispose()
    finally:
        await gate_engine.dispose()


@pytest.fixture(scope="session")
def _migrated_schema() -> None:
    """Build the schema once for the whole session.

    Synchronous + ``asyncio.run`` on purpose: this owns its own short-lived loop and
    disposes its engine before returning, so nothing is shared across the
    function-scoped loops the tests themselves use (asyncpg connections are
    loop-bound, and a session-scoped engine handed to per-function loops is exactly
    how that breaks).
    """
    try:
        asyncio.run(_build_schema())
    except _DB_UNAVAILABLE as exc:
        # In an environment that *requires* the DB (CI sets REQUIRE_DB=1), a missing
        # database must be a hard failure, never a silent skip that lets the whole
        # integration suite go green without running (INT-23). Locally it still skips.
        message = f"Test database unavailable ({TEST_DATABASE_URL}): {exc}"
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(
                message + " — REQUIRE_DB is set; the DB-backed suite must not skip.",
                pytrace=False,
            )
        pytest.skip(message)


@pytest_asyncio.fixture(loop_scope="function")
async def async_db(_migrated_schema: None) -> AsyncSession:
    """Async DB session against the migrated schema, emptied for this test.

    The schema is built once per session (see ``_migrated_schema``); this fixture
    only guarantees the *data* is empty. Migration/loop errors propagate.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    await _truncate_all(engine)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(scope="session")
def catalog_snapshot() -> list:
    """The movement catalog as plain data, built from the seeder source — no database.

    Pure prescriber tests call `recommend_next_session` directly. Passing this as `catalog=`
    makes them exercise real slot resolution; omitting it silently drops them onto the
    equipment-map fallback, which is a different code path and proves nothing about selection.
    """
    from app.data.exercise_bulk import bulk_exercises
    from app.logic.exercise_slot import CatalogExercise
    from app.scripts.seed_exercises import EXERCISES

    return [
        CatalogExercise(
            name=row["name"],
            modality=row["modality"],
            movement_pattern=row["movement_pattern"],
            load_type=row["load_type"],
            pattern_family=row.get("pattern_family"),
            equipment_required=tuple(row.get("equipment_required") or ()),
            sport_domains=tuple(row.get("sport_domains") or ()),
            weak_point_tags=tuple(row.get("weak_point_tags") or ()),
            skill_demand=row.get("skill_demand") or 0.5,
            e1rm_benchmark_code=row.get("e1rm_benchmark_code"),
        )
        for row in list(EXERCISES) + list(bulk_exercises())
    ]


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_exercise_catalog(async_db: AsyncSession) -> None:
    """Populate the movement catalog for tests that exercise prescription.

    Exercise selection resolves template slots against this table (ADR-0016), so a test that
    prescribes a session against an EMPTY catalog silently falls back to the equipment map and
    proves nothing about selection. Production always has the catalog — `seed_catalog` runs on
    every container boot — so seeding it here is faithful rather than convenient.

    Opt-in rather than autouse: several tests assert on catalog row counts and would break if
    181 rows appeared underneath them.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.scripts import seed_exercises

    factory = async_sessionmaker(async_db.bind, expire_on_commit=False)
    original = seed_exercises.AsyncSessionLocal
    seed_exercises.AsyncSessionLocal = factory  # type: ignore[assignment]
    try:
        await seed_exercises.seed()
    finally:
        seed_exercises.AsyncSessionLocal = original  # type: ignore[assignment]
    await async_db.rollback()


@pytest_asyncio.fixture(loop_scope="function")
async def http_client(async_db: AsyncSession) -> httpx.AsyncClient:
    """Async HTTP client wired to the FastAPI app with the test DB session injected.

    No lifespan is triggered — the schema is owned by the ``async_db`` fixture.
    """
    from app.main import app

    async def _override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_db, None)
