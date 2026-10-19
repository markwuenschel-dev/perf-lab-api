"""
app/core/db.py
Async database engine and session management.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Create the async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,      # revalidate pooled conns (managed Postgres drops idle ones)
    pool_size=10,
    max_overflow=20,
)


# Base class for all models
class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


logger = logging.getLogger("perflab.db")

_QUARANTINE_REASON = "perflab.session.quarantined"
_QUARANTINE_OWNER = "perflab.session.quarantine_owner"
# Operations still running on quarantined sessions, and the closes deferred until they end.
_held: set[asyncio.Future[None]] = set()


def _keep(task: asyncio.Future[None]) -> None:
    _held.add(task)
    task.add_done_callback(_held.discard)


def quarantine_session(session: AsyncSession, owner: asyncio.Future[None] | None, why: str) -> None:
    """Mark ``session`` unusable: an operation (``owner``) may still be running on it.

    Nothing may touch the session while ``owner`` runs: SQLAlchemy refuses a state change, a
    close included, while another is in progress. ``session_scope`` closes it once ``owner`` ends.
    """
    session.info[_QUARANTINE_REASON] = why
    if owner is not None and not owner.done():
        session.info[_QUARANTINE_OWNER] = owner
        _keep(owner)


def quarantine_reason(session: AsyncSession) -> str | None:
    return session.info.get(_QUARANTINE_REASON)


async def _close_quarantined(session: AsyncSession) -> None:
    try:
        await session.close()
    except Exception as exc:  # noqa: BLE001 — the owner's own result must stand
        logger.critical("closing a quarantined session failed: %r", exc)


async def release_session(session: AsyncSession) -> None:
    """Close ``session`` as its owner exits. A quarantined one is closed only after the operation
    holding it ends, and a failure to close it is logged, never raised over the owner's result."""
    if quarantine_reason(session) is None:
        await asyncio.shield(asyncio.ensure_future(session.close()))
        return
    owner: asyncio.Future[None] | None = session.info.get(_QUARANTINE_OWNER)
    if owner is None or owner.done():
        await _close_quarantined(session)
        return
    logger.error("deferring the close of a quarantined session until its operation ends")
    owner.add_done_callback(lambda _o: _keep(asyncio.ensure_future(_close_quarantined(session))))


@asynccontextmanager
async def session_scope(
    factory: Callable[[], AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """A session closed by ``release_session`` on exit (``AsyncSessionLocal`` by default)."""
    session = (factory or AsyncSessionLocal)()
    try:
        yield session
    finally:
        await release_session(session)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    async with session_scope() as session:
        yield session