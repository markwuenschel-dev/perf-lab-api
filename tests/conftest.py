"""
Pytest configuration and shared fixtures.

DB fixtures use a dedicated test database (perflab_test). If the DB is
unavailable the tests are skipped gracefully — they're integration tests
that require a running Postgres instance.
"""
import os
import asyncio

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Import all models so Base.metadata is populated before create_all
import app.models  # noqa: F401 — registers all models with Base
from app.core.db import Base, get_db

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
    Async DB session fixture.

    Creates all tables before the test, yields a session, and drops all tables
    after. Each test function gets a clean DB slate.

    Skip gracefully if the DB is unavailable.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    try:
        # Clean slate for every test — handles leftover data from crashed runs
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
            await conn.execute(sa.text("GRANT ALL ON SCHEMA public TO PUBLIC"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Test DB unavailable ({TEST_DATABASE_URL}): {exc}")

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        yield session

    # Drop everything — use CASCADE to handle circular FKs between
    # planned_sessions and workout_logs cleanly.
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.execute(sa.text("GRANT ALL ON SCHEMA public TO PUBLIC"))
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
