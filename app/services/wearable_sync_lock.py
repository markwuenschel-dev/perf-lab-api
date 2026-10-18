"""One wearable sync per connection at a time, across workers and processes (Wave 4, L1).

A sync refreshes OAuth credentials and then pulls days, committing several times. Two syncs of
one connection (app load and the check-in opening together, or the manual CLI beside a user)
would both refresh with the same refresh token. The P6 generation fence stops a refresh from
overwriting a *relink*, but a refresh never advances the generation, so it cannot stop a second
refresh. This module serializes them.

**The lock.** A session-level ``pg_advisory_lock(WSY1, connection_id)`` held on a *dedicated*
connection for the whole attempt. A session-level lock survives COMMIT and ROLLBACK and lives on
the physical connection, so it is never taken through the caller's ``AsyncSession`` (whose
connection goes back to the pool at every commit). The dedicated connection comes from a
**NullPool** engine on the same URL, so a caller waiting for the lock never holds one of the app
pool's connections: the holder's own statements can always get one, and contention cannot
deadlock on pool exhaustion.

**Waiting (one budget, ``ACQUIRE_BUDGET_S``).** In order: a per-process lock per connection id
(queued callers in one process hold no database connection at all), a per-process capacity
semaphore (``LOCK_CONNECTIONS_PER_PROCESS`` dedicated connections at most, for any mix of ids:
the box's Postgres is shared with other apps), opening the dedicated connection, and the
advisory lock itself under ``SET LOCAL lock_timeout`` set to what remains. ``SET LOCAL`` reverts
at the COMMIT that ends the acquiring transaction, while the session-level lock survives it, so
the connection then sits idle and not in a transaction. Expiry raises ``SyncBusy``.

The capacity bound is per process: each API worker and each CLI run has its own.

**Release.** Runs as its own task, and the caller awaits it to the end however often it is
cancelled (``finish_despite_cancellation``): first every protected write (``protect``) finishes, then ``pg_advisory_unlock`` must return
true; if it returns false, raises, or times out, the physical connection is invalidated
(closing it ends the session, which frees the lock). Only then are the capacity slot and the
per-id lock released. A protected write never runs after the unlock: the unlock waits for it,
and if it cannot finish within ``CLEANUP_BUDGET_S`` it is cancelled and awaited, and then the
connection is invalidated and closed (which frees the lock; a closed connection never reconnects).
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger("perflab.wearable.sync_lock")

#: Advisory-lock namespace, "WSY1" (within signed int4; distinct from STA1, EKF1, WSH1).
SYNC_LOCK_NAMESPACE = 0x57535931

#: Everything a caller may wait before its sync starts: the per-id queue, the capacity slot,
#: opening the dedicated connection, and the advisory lock.
ACQUIRE_BUDGET_S = 30.0
#: Dedicated lock connections one process may hold at once, across all connection ids.
LOCK_CONNECTIONS_PER_PROCESS = 2
#: The bound on release: protected writes, then the unlock. Past it, the connection is invalidated.
CLEANUP_BUDGET_S = 10.0
#: Server-side bound on each statement a protected write runs on the lock connection.
PROTECTED_STATEMENT_TIMEOUT_MS = 8000

# Postgres "lock_not_available": what lock_timeout raises.
_LOCK_NOT_AVAILABLE = "55P03"
# How much sooner than the asyncio deadline the server-side lock_timeout fires.
_SERVER_FIRST_MS = 200

T = TypeVar("T")


class SyncBusy(RuntimeError):
    """Another sync of this connection held the lock for the whole acquisition budget."""


@dataclass
class _ProcessState:
    """Per event loop: asyncio primitives and asyncpg connections are bound to one loop."""

    capacity: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(LOCK_CONNECTIONS_PER_PROCESS))
    keyed: dict[int, tuple[asyncio.Lock, int]] = field(
        default_factory=lambda: dict[int, tuple[asyncio.Lock, int]]())
    engines: dict[str, AsyncEngine] = field(default_factory=lambda: dict[str, AsyncEngine]())

    def engine_for(self, url: URL) -> AsyncEngine:
        key = url.render_as_string(hide_password=False)
        engine = self.engines.get(key)
        if engine is None:
            engine = self.engines[key] = create_async_engine(url, poolclass=NullPool)
        return engine

    def enter_key(self, connection_id: int) -> asyncio.Lock:
        lock, users = self.keyed.get(connection_id, (asyncio.Lock(), 0))
        self.keyed[connection_id] = (lock, users + 1)
        return lock

    def leave_key(self, connection_id: int) -> None:
        lock, users = self.keyed[connection_id]
        if users <= 1:
            del self.keyed[connection_id]
        else:
            self.keyed[connection_id] = (lock, users - 1)


_states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _ProcessState] = (
    weakref.WeakKeyDictionary())


def _state() -> _ProcessState:
    loop = asyncio.get_running_loop()
    state = _states.get(loop)
    if state is None:
        state = _states[loop] = _ProcessState()
    return state


async def finish_despite_cancellation(task: asyncio.Future[T]) -> T:
    """Await ``task`` to its end even if the caller is cancelled, any number of times.

    A cancellation of the caller is held back until ``task`` is done, then re-raised once. For
    cleanup that must complete before what follows it (an unlock, a capacity release). ``task``
    must be bounded: the caller cannot be cancelled out of this wait.
    """
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def _is_lock_timeout(exc: DBAPIError) -> bool:
    orig = exc.orig
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _LOCK_NOT_AVAILABLE


class SyncLock:
    """A held sync lock: its dedicated connection, plus the writes release must wait for."""

    def __init__(self, conn: AsyncConnection, connection_id: int) -> None:
        self.conn = conn
        self.connection_id = connection_id
        self._protected: set[asyncio.Task[Any]] = set()

    async def protect(self, work: Callable[[AsyncConnection], Awaitable[T]]) -> T:
        """Run ``work`` on the lock connection so that cancelling the caller cannot abandon it.

        For a write that must not be lost once its inputs exist (new OAuth credentials that
        Oura has already issued). ``work`` runs in its own task; if the caller is cancelled
        meanwhile, the task carries on and release waits for it before unlocking.
        """
        task = asyncio.ensure_future(work(self.conn))
        self._protected.add(task)
        task.add_done_callback(self._protected.discard)
        return await asyncio.shield(task)

    async def drain(self) -> bool:
        """Wait for protected writes. False when one could not finish within the budget.

        A write still running at the budget is cancelled and awaited here, before the caller
        invalidates the connection: an invalidated connection reconnects on its next use, so a
        write left running could otherwise land on a new connection after the unlock.
        """
        pending = set(self._protected)
        if not pending:
            return True
        done, still = await asyncio.wait(pending, timeout=CLEANUP_BUDGET_S)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.warning("protected write on connection %s failed: %r",
                               self.connection_id, task.exception())
        if not still:
            return True
        for task in still:
            task.cancel()
        await asyncio.wait(still, timeout=CLEANUP_BUDGET_S)
        logger.error("sync lock %s: a protected write did not finish within %ss and was aborted; "
                     "its outcome is unknown", self.connection_id, CLEANUP_BUDGET_S)
        return False


async def _release(lock: SyncLock | None, conn: AsyncConnection | None, connection_id: int,
                   state: _ProcessState, *, locked: bool, held_slot: bool) -> None:
    try:
        if conn is not None:
            confirmed = False
            drained = await lock.drain() if lock is not None else True
            if locked and drained:
                try:
                    async with asyncio.timeout(CLEANUP_BUDGET_S):
                        unlocked = (await conn.execute(text(
                            "SELECT pg_advisory_unlock(:ns, :id)"),
                            {"ns": SYNC_LOCK_NAMESPACE, "id": connection_id})).scalar_one()
                        await conn.commit()
                    confirmed = bool(unlocked)
                except Exception as exc:  # noqa: BLE001 — any failure means "not confirmed"
                    logger.warning("sync lock %s: unlock failed: %r", connection_id, exc)
            if confirmed:
                logger.debug("sync lock %s released", connection_id)
                await conn.close()
            else:
                # Not locked as far as we know, a protected write that had to be aborted, or an
                # unlock we could not confirm: closing the physical connection is the one release
                # that cannot fail to free the lock. A closed connection never reconnects.
                if locked:
                    logger.warning("sync lock %s: release not confirmed; invalidating its "
                                   "connection", connection_id)
                await conn.invalidate()
                await conn.close()
                logger.debug("sync lock %s released by invalidation", connection_id)
    except Exception as exc:  # noqa: BLE001 — release must always reach the slot release below
        logger.error("sync lock %s: cleanup failed: %r", connection_id, exc)
    finally:
        if held_slot:
            state.capacity.release()


@asynccontextmanager
async def hold_sync_lock(
    url: URL, connection_id: int, *, acquire_budget_s: float | None = None
) -> AsyncIterator[SyncLock]:
    """Hold the sync lock for ``connection_id`` for the body of the ``async with``."""
    budget = ACQUIRE_BUDGET_S if acquire_budget_s is None else acquire_budget_s
    state = _state()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    key_lock = state.enter_key(connection_id)
    held_key = held_slot = locked = False
    conn: AsyncConnection | None = None
    lock: SyncLock | None = None
    try:
        try:
            async with asyncio.timeout_at(deadline):
                await key_lock.acquire()
                held_key = True
                await state.capacity.acquire()
                held_slot = True
                conn = await state.engine_for(url).connect()
                # Just short of the budget, so Postgres ends a lock wait itself (55P03) and the
                # asyncio deadline, which would cancel the query mid-wait, stays a backstop.
                remaining_ms = max(1, int((deadline - loop.time()) * 1000) - _SERVER_FIRST_MS)
                # SET takes no bind parameters; remaining_ms is an int computed here.
                await conn.execute(text(f"SET LOCAL lock_timeout = '{remaining_ms}ms'"))
                await conn.execute(text("SELECT pg_advisory_lock(:ns, :id)"),
                                   {"ns": SYNC_LOCK_NAMESPACE, "id": connection_id})
                locked = True
                await conn.commit()
        except TimeoutError as exc:
            raise SyncBusy(f"wearable connection {connection_id} is already syncing") from exc
        except DBAPIError as exc:
            if _is_lock_timeout(exc):
                raise SyncBusy(f"wearable connection {connection_id} is already syncing") from exc
            raise
        lock = SyncLock(conn, connection_id)
        logger.debug("sync lock %s acquired", connection_id)
        yield lock
    finally:
        # A cancelled or timed-out acquire may have been granted the lock as it was cancelled,
        # so "locked" is only a lower bound: an unconfirmed lock is released by invalidation.
        cleanup = asyncio.ensure_future(_release(
            lock, conn, connection_id, state, locked=locked and lock is not None,
            held_slot=held_slot))
        try:
            # The caller returns only after the full release, however often it is cancelled.
            await finish_despite_cancellation(cleanup)
        finally:
            if held_key:
                key_lock.release()
            state.leave_key(connection_id)
