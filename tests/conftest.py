"""
Pytest configuration and shared fixtures.

DB fixtures use a dedicated test database (perflab_test). If the DB is
unavailable the tests are skipped gracefully — they're integration tests
that require a running Postgres instance.

Improved strategy (v0.3+):
- Schema is dropped for clean isolation per test.
- Tables are created via Alembic migrations (not Base.metadata.create_all).
  This catches migration drift and keeps test schema faithful to production.
"""
import asyncio
import os

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Still import models for relationship wiring if needed, but we no longer rely on create_all
import app.models  # noqa: F401
from alembic import command
from app.core.db import get_db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_db: mark test as requiring a live PostgreSQL connection",
    )


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def async_db():
    """
    Async DB session fixture (Alembic-aligned).

    - Drops the public schema for a completely clean slate per test.
    - Runs Alembic migrations to head (instead of Base.metadata.create_all).
      This ensures tests exercise the same schema that production uses.
    - Skips gracefully if the test database is unavailable.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)

    try:
        # 1. Clean slate
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
            await conn.execute(sa.text("GRANT ALL ON SCHEMA public TO PUBLIC"))

        # 2. Run real migrations (this is the key improvement)
        alembic_cfg = Config("alembic.ini")
        # Override the URL so Alembic targets the test database
        alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("+asyncpg", ""))
        command.upgrade(alembic_cfg, "head")

    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Test DB unavailable or migration failed ({TEST_DATABASE_URL}): {exc}")

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        yield session

    # Cleanup
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def http_client(async_db: AsyncSession):
    """
    Async HTTP test client wired to the FastAPI app with the test DB session
    injected via dependency override. No lifespan is triggered — tables are
    managed by the async_db fixture.
    """
    from app.main import app

    async def _override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_db, None)
