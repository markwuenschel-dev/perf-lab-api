"""
Pytest configuration and shared fixtures.

DB fixtures use a dedicated test database (perflab_test). If the database server
is genuinely unreachable the integration tests skip gracefully; any *other*
failure (migration error, event-loop misuse, schema drift) fails loudly rather
than masquerading as a skip.

Strategy:
- Schema is dropped and recreated for clean isolation per test.
- Tables are created via Alembic migrations (not Base.metadata.create_all),
  so tests exercise the same schema production uses and catch migration drift.
- Migrations run on the *test* connection via ``config.attributes["connection"]``
  (see ``alembic/env.py``), so there is no nested ``asyncio.run()`` and no
  dependency on the app's configured DATABASE_URL.
"""
import os

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Import models so their metadata/relationships are registered for migrations.
import app.models  # noqa: F401
from alembic import command
from app.core.db import get_db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)

# Connection-level failures that legitimately mean "no database available" — the
# only conditions under which an integration test is allowed to skip. Anything
# else (e.g. a broken migration) must surface as a real error.
_DB_UNAVAILABLE = (OSError, ConnectionError, OperationalError, InterfaceError)


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


@pytest_asyncio.fixture(loop_scope="function")
async def async_db() -> AsyncSession:
    """Async DB session against a freshly-migrated, isolated schema.

    Drops+recreates ``public`` for a clean slate, then runs real Alembic
    migrations on the test connection. Skips only if the database server is
    unreachable; migration/loop errors propagate.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
            await conn.execute(sa.text("GRANT ALL ON SCHEMA public TO PUBLIC"))
        async with engine.connect() as conn:
            await conn.run_sync(_run_upgrade)
    except _DB_UNAVAILABLE as exc:
        await engine.dispose()
        pytest.skip(f"Test database unavailable ({TEST_DATABASE_URL}): {exc}")

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session

    await engine.dispose()


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
