"""Wearable-sync orchestration — provider-independent (OAuth + PAT).

Ties the provider adapter, the encrypted token store (``WearableConnection``), and
the wellness sink together:

- OAuth: an authorization is a durable attempt (``app.services.wearable_oauth``, ADR-0075);
  its success writes the connection through ``upsert_connection`` here.
- PAT: ``connect_pat`` validates a Personal Access Token and stores it the same way.
- Every successful link advances the row's ``generation``; a token refresh never does, and
  writes only while the generation it read is unchanged, so a refresh that raced a reconnect
  cannot overwrite the new credentials.
- Sync: ``sync_connection`` holds the connection's sync lock (``wearable_sync_lock``), refreshes
  the OAuth token as needed, pulls new days, and upserts them via the canonical
  ``readiness_service.upsert_wellness_sample`` sink (source = the connection's own provider slug),
  advancing ``last_sync_at``. Syncs run when the athlete shows up (the web syncs a stale
  connection on load and when the check-in opens); there is no nightly job, so nothing keeps
  data fresh unattended (ADR-0027, superseded). ``sync_all`` drives every connection for the
  manual catch-up CLI (``app.scripts.sync_wearables``).

Tokens are Fernet-encrypted at rest (``app.core.crypto``); this module and
``wearable_oauth`` are the only places that handle them.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core import crypto
from app.integrations.base import TokenBundle, WearableAdapter
from app.integrations.registry import adapter_for
from app.logic.prescription_evidence import utc_naive
from app.models.wearable_connection import WearableConnection
from app.schemas.wellness import WellnessSampleIn
from app.services import readiness_service, wearable_sync_lock
from app.services.wearable_sync_lock import (
    PROTECTED_STATEMENT_TIMEOUT_MS,
    SyncLock,
    hold_sync_lock,
)
from app.services.wellness_shadow_hooks import run_wellness_shadows

logger = logging.getLogger("perflab.wearable")

# Refresh an OAuth access token when it is within this window of expiring.
_REFRESH_BUFFER = timedelta(minutes=5)

# How many trailing days a first sync (no watermark) pulls.
DEFAULT_SYNC_DAYS = 7

#: The provider assumed when a caller does not name one. Still Oura because it is the only
#: registered integration; every signature below keeps taking an explicit provider, so this
#: is a convenience default rather than an assumption baked into the logic.
DEFAULT_PROVIDER = "oura"


def _adapter(provider: str = DEFAULT_PROVIDER) -> WearableAdapter:
    """Resolve a provider slug through the registry.

    Was an ``if provider == "oura"`` ladder plus a direct ``OuraAdapter`` import, which made
    this shared service a place every new integration had to edit — and falsified
    ``integrations/base.py``'s claim that the sync service never imports a concrete provider.
    """
    return adapter_for(provider)


def adapter(provider: str = DEFAULT_PROVIDER) -> WearableAdapter:
    """The provider's adapter, for callers outside this module (routes). Resolves through
    ``_adapter`` at call time, so the one seam tests replace covers every caller."""
    return _adapter(provider)


# --- Connection persistence ------------------------------------------------------


async def get_connection(
    db: AsyncSession, user_id: int, provider: str = DEFAULT_PROVIDER
) -> WearableConnection | None:
    # populate_existing: the session keeps objects across commits (expire_on_commit=False), so
    # without it a second read in one session would return the first read's values.
    return (
        await db.execute(
            select(WearableConnection)
            .where(
                WearableConnection.user_id == user_id,
                WearableConnection.provider == provider,
            )
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


class ConnectionReplaced(RuntimeError):
    """A refresh found its connection relinked or deleted meanwhile; its tokens were dropped."""


async def upsert_connection(
    db: AsyncSession,
    user_id: int,
    *,
    provider: str,
    auth_type: str,
    access_enc: str,
    refresh_enc: str | None,
    expires_at: datetime | None,
    scope: str | None,
) -> tuple[int, int]:
    """Link (user, provider) to these already-encrypted credentials. Returns (id, generation).

    One statement, so two links racing each other both count: a new row starts at generation 1
    (including one recreated after a disconnect), an existing row goes to generation + 1. The
    caller commits.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    row = (await db.execute(text(
        "INSERT INTO wearable_connections (user_id, provider, auth_type, access_token_enc, "
        "refresh_token_enc, expires_at, scope, created_at, updated_at, generation) "
        "VALUES (:u, :p, :a, :acc, :ref, :exp, :scope, :now, :now, 1) "
        "ON CONFLICT ON CONSTRAINT uq_wearable_user_provider DO UPDATE SET "
        "auth_type = EXCLUDED.auth_type, access_token_enc = EXCLUDED.access_token_enc, "
        "refresh_token_enc = EXCLUDED.refresh_token_enc, expires_at = EXCLUDED.expires_at, "
        "scope = EXCLUDED.scope, updated_at = EXCLUDED.updated_at, "
        "generation = wearable_connections.generation + 1 "
        "RETURNING id, generation"
    ), {"u": user_id, "p": provider, "a": auth_type, "acc": access_enc, "ref": refresh_enc,
        "exp": expires_at, "scope": scope, "now": now})).one()
    return row.id, row.generation


async def connect_pat(
    db: AsyncSession, user_id: int, token: str, provider: str = DEFAULT_PROVIDER
) -> WearableConnection:
    """Validate a Personal Access Token with a live probe, then store it."""
    adapter = _adapter(provider)
    today = _utc_today()
    # A cheap probe that also confirms the token has the right scope.
    await adapter.fetch_daily_wellness(token, today - timedelta(days=1), today)
    await upsert_connection(
        db, user_id, provider=provider, auth_type="pat", access_enc=crypto.encrypt(token),
        refresh_enc=None, expires_at=None, scope=None,
    )
    await db.commit()
    conn = await get_connection(db, user_id, provider)
    if conn is None:  # pragma: no cover - the upsert just committed it
        raise RuntimeError("PAT connection vanished after its commit")
    return conn


async def disconnect(db: AsyncSession, user_id: int, provider: str = DEFAULT_PROVIDER) -> bool:
    conn = await get_connection(db, user_id, provider)
    if conn is None:
        return False
    await db.delete(conn)
    await db.commit()
    return True


# --- Sync ------------------------------------------------------------------------
#
# One attempt (Wave 4, L1; ADR-0027 as superseded):
#   0. the caller's session ends its read-only transaction (a pending write is refused);
#   1. the sync lock for the connection id is held (``wearable_sync_lock``; up to 30 s to get it);
#   2. under a separate work budget, one snapshot of the row — (id, generation) frozen — is read
#      on the lock connection and used for everything after: the refresh is saved only while that
#      generation still stands, and the watermark advances only on it. A newer generation is never
#      adopted mid-attempt: a relink makes this attempt end in ConnectionReplaced, and its tokens
#      and watermark are left to the new link;
#   3. no transaction is open on either connection during a provider call.
# Wellness rows committed before a replacement is detected stay: they are the athlete's own data,
# and a final ConnectionReplaced does not undo them.

#: The work budget once the lock is held: the token, the fetch (which ``lock_timeout`` does not
#: bound, nor Oura's pagination), every day's write, and the watermark.
SYNC_WORK_BUDGET_S = 60.0


class SyncTimedOut(RuntimeError):
    """The attempt held the lock but did not finish within ``SYNC_WORK_BUDGET_S``."""


@dataclass
class SyncProgress:
    """What one attempt durably did, readable after it fails.

    ``rows_confirmed`` counts wellness rows whose commit returned. A write still in flight when
    the attempt failed (a timeout or a lost connection mid-commit) may or may not have committed:
    it is ``in_doubt_day``, never assumed rolled back.
    """

    rows_confirmed: int = 0
    in_doubt_day: date_cls | None = None

    def acknowledge(self) -> None:
        """The in-flight day's COMMIT returned: it is durable, whatever happens next."""
        self.rows_confirmed += 1
        self.in_doubt_day = None


@dataclass(frozen=True)
class SyncOutcome:
    rows_written: int
    last_sync_at: datetime


@dataclass(frozen=True)
class _Snapshot:
    id: int
    user_id: int
    provider: str
    auth_type: str
    access_token_enc: str
    refresh_token_enc: str | None
    expires_at: datetime | None
    generation: int
    last_sync_at: datetime | None


async def _end_read_only_transaction(db: AsyncSession) -> None:
    """End the caller's open transaction, refusing one that holds a write.

    Every caller reaches a sync having only read (the route's auth and connection lookups,
    and ``sync_targets``' id list). Committing a read-only transaction ends it
    without discarding loaded objects; a transaction that has written anything has a Postgres
    transaction id, and ORM changes not yet flushed show in the session, so both are refused
    rather than committed as a side effect of a sync. A quarantined session is refused untouched.
    """
    if why := db.info.get(_QUARANTINED):
        raise SessionQuarantined(f"this session cannot be used for another sync: {why}")
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("a wearable sync needs a session with no pending changes")
    if not db.in_transaction():
        return
    xid = (await db.execute(text("SELECT pg_current_xact_id_if_assigned()"))).scalar()
    if xid is not None:
        raise RuntimeError("a wearable sync needs a session whose transaction has not written; "
                           "commit or roll it back first")
    await db.commit()


class SessionQuarantined(RuntimeError):
    """The session's cleanup after an earlier sync could not be confirmed; it is never reused."""


_QUARANTINED = "wearable_sync.quarantined"
# Cleanup operations still running on a quarantined session, kept referenced until they end.
_abandoned: set[asyncio.Future[None]] = set()


def _quarantine(db: AsyncSession, op: asyncio.Future[None] | None, why: str) -> None:
    db.info[_QUARANTINED] = why
    if op is not None and not op.done():
        _abandoned.add(op)
        op.add_done_callback(_abandoned.discard)
    logger.critical("wearable sync: the work session is quarantined and will not be reused: %s", why)


async def _end_work_session(db: AsyncSession) -> None:
    """End whatever the work session left open, within a bound, never replacing the attempt's
    own error.

    Runs while the sync lock and its capacity slot are still held, so it must finish: it runs in
    its own task, awaited to the end however often the caller is cancelled, and each step is
    bounded by ``CLEANUP_BUDGET_S``. A rollback that does not return is cancelled; once it has
    ended, the session's connection is invalidated (closed rather than returned to the pool).
    A rollback that is still running after its cancellation owns the session, so the session is
    never touched again: it is quarantined, and every later sync on it is refused.
    """
    if db.info.get(_QUARANTINED):
        return
    await wearable_sync_lock.finish_despite_cancellation(
        asyncio.ensure_future(_bounded_rollback(db)))


async def _bounded_rollback(db: AsyncSession) -> None:
    budget = wearable_sync_lock.CLEANUP_BUDGET_S
    rollback = asyncio.ensure_future(db.rollback())
    done, _ = await asyncio.wait({rollback}, timeout=budget)
    if done:
        exc = None if rollback.cancelled() else rollback.exception()
        if exc is None:
            return
        logger.warning("wearable sync: rollback after the attempt failed: %r", exc)
    else:
        rollback.cancel()
        done, _ = await asyncio.wait({rollback}, timeout=budget)
        if not done:
            # Still unwinding: invalidating now would race it (SQLAlchemy refuses a state change
            # while another operation is in progress), and so would any later use of the session.
            _quarantine(db, rollback, f"its rollback did not end within {budget}s of being cancelled")
            return
        logger.error("wearable sync: rollback did not finish within %ss; invalidating the work "
                     "session's connection", budget)
    invalidate = asyncio.ensure_future(db.invalidate())
    done, _ = await asyncio.wait({invalidate}, timeout=budget)
    if not done:
        invalidate.cancel()
        _quarantine(db, invalidate, f"invalidating its connection did not finish within {budget}s")
    elif not invalidate.cancelled() and invalidate.exception() is not None:
        _quarantine(db, None, f"invalidating its connection failed: {invalidate.exception()!r}")


async def _read_snapshot(conn: AsyncConnection, connection_id: int) -> _Snapshot | None:
    row = (await conn.execute(text(
        "SELECT id, user_id, provider, auth_type, access_token_enc, refresh_token_enc, "
        "expires_at, generation, last_sync_at FROM wearable_connections WHERE id = :i"
    ), {"i": connection_id})).mappings().one_or_none()
    await conn.commit()
    return None if row is None else _Snapshot(**row)


async def _save_refreshed_credentials(
    conn: AsyncConnection, snap: _Snapshot, tokens: TokenBundle
) -> bool:
    """Store a refresh's credentials while the snapshot's generation stands. False if relinked."""
    await conn.execute(text(f"SET LOCAL statement_timeout = {PROTECTED_STATEMENT_TIMEOUT_MS}"))
    written = (await conn.execute(text(
        "UPDATE wearable_connections SET access_token_enc = :acc, "
        "refresh_token_enc = COALESCE(:ref, refresh_token_enc), expires_at = :exp, "
        "scope = COALESCE(:scope, scope), updated_at = :now "
        "WHERE id = :i AND generation = :g RETURNING id"
    ), {
        "i": snap.id, "g": snap.generation,
        "acc": crypto.encrypt(tokens.access_token),
        "ref": crypto.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
        "exp": stored_expiry(tokens.expires_at), "scope": tokens.scope or None,
        "now": datetime.now(UTC).replace(tzinfo=None),
    })).first()
    await conn.commit()
    return written is not None


async def _access_token(lock: SyncLock, snap: _Snapshot) -> str:
    """A usable access token for the snapshot, refreshing an expiring OAuth token."""
    access = crypto.decrypt(snap.access_token_enc)
    if snap.auth_type != "oauth" or snap.expires_at is None:
        return access  # PATs don't expire / can't refresh
    if _aware(snap.expires_at) - datetime.now(UTC) > _REFRESH_BUFFER:
        return access
    if not snap.refresh_token_enc:
        logger.warning("Oura connection %s expired with no refresh token", snap.id)
        return access
    tokens = await _adapter(snap.provider).refresh_tokens(crypto.decrypt(snap.refresh_token_enc))
    # Oura has issued (and may have rotated) credentials: saving them must outlive a timeout or
    # a cancelled request, and the lock is held until the save finishes or is aborted.
    saved = await lock.protect(lambda conn: _save_refreshed_credentials(conn, snap, tokens))
    if not saved:
        raise ConnectionReplaced(f"connection {snap.id} was relinked during its refresh")
    return tokens.access_token


async def sync_connection(
    db: AsyncSession, connection_id: int, *, days: int = DEFAULT_SYNC_DAYS,
    progress: SyncProgress | None = None,
) -> SyncOutcome:
    """Pull new days for one connection into WellnessSample, holding its sync lock.

    Raises ``SyncBusy`` if another sync held the lock for the whole acquisition budget,
    ``SyncTimedOut`` past the work budget, and ``ConnectionReplaced`` if the connection was
    removed or relinked before the attempt finished. Pass ``progress`` to learn what a failed
    attempt had already committed.
    """
    progress = progress if progress is not None else SyncProgress()
    await _end_read_only_transaction(db)
    async with hold_sync_lock(db.get_bind().engine.url, connection_id) as lock:
        work = asyncio.timeout(SYNC_WORK_BUDGET_S)
        try:
            async with work:
                return await _sync_locked(db, lock, connection_id, days, progress)
        except TimeoutError as exc:
            if work.expired():
                raise SyncTimedOut(
                    f"wearable connection {connection_id} did not finish syncing within "
                    f"{SYNC_WORK_BUDGET_S:g}s") from exc
            raise
        finally:
            # Outside the expired work scope, and still under the lock and its capacity slot.
            await _end_work_session(db)


async def _sync_locked(
    db: AsyncSession, lock: SyncLock, connection_id: int, days: int, progress: SyncProgress
) -> SyncOutcome:
    snap = await _read_snapshot(lock.conn, connection_id)
    if snap is None:
        # Removed while this attempt waited. A row recreated since has a new id and its own lock;
        # it is never looked up by (user, provider) here.
        raise ConnectionReplaced(f"connection {connection_id} was removed before its sync")
    access = await _access_token(lock, snap)

    end = _utc_today()
    # Start from the day before the last sync (to catch late-arriving data), else a trailing
    # window on first run. A watermark always wins over ``days``: this is catch-up, not backfill.
    if snap.last_sync_at is not None:
        start = _aware(snap.last_sync_at).date() - timedelta(days=1)
    else:
        start = end - timedelta(days=days)
    if start > end:
        start = end

    readings = await _adapter(snap.provider).fetch_daily_wellness(access, start, end)
    for r in readings:
        payload = WellnessSampleIn(
            date=r.day,
            source=snap.provider,
            hrv_ms=r.hrv_ms,
            hrv_metric=r.hrv_metric,
            sleep_hours=r.sleep_hours,
            sleep_quality=r.sleep_quality,
            resting_hr=r.resting_hr,
            soreness=r.soreness,
            mood=r.mood,
            stress=r.stress,
            # Freshness and provider-reported reliability, when the adapter supplies them.
            # Both stay None otherwise rather than being defaulted.
            measured_at=r.measured_at,
            quality=r.quality,
            raw=r.raw or None,
        )
        progress.in_doubt_day = r.day
        sample = await readiness_service.upsert_wellness_sample(
            db, snap.user_id, payload, on_committed=progress.acknowledge)
        # That day is committed: run its shadows now, before the next day's write (P7). Oura
        # supplies no soreness, so the EKF records an explicit skip.
        await run_wellness_shadows(db, sample, assimilate_soreness=False)

    stamped = (await lock.conn.execute(text(
        "UPDATE wearable_connections SET last_sync_at = :now "
        "WHERE id = :i AND generation = :g RETURNING last_sync_at"
    ), {"i": snap.id, "g": snap.generation,
        "now": datetime.now(UTC).replace(tzinfo=None)})).scalar_one_or_none()
    await lock.conn.commit()
    if stamped is None:
        raise ConnectionReplaced(
            f"connection {snap.id} was removed or relinked during its sync; "
            f"{progress.rows_confirmed} wellness rows were kept")
    return SyncOutcome(rows_written=progress.rows_confirmed, last_sync_at=stamped)


@dataclass(frozen=True)
class ConnectionSyncReport:
    key: str
    rows_confirmed: int
    in_doubt_day: date_cls | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def sync_one(
    db: AsyncSession, connection_id: int, key: str, *, days: int = DEFAULT_SYNC_DAYS
) -> ConnectionSyncReport:
    """Sync one connection and report it, failure included: what was saved, and what is unknown."""
    progress = SyncProgress()
    try:
        await sync_connection(db, connection_id, days=days, progress=progress)
    except Exception as exc:  # noqa: BLE001 — a failure is reported, with its partial success
        logger.exception("Wearable sync failed for %s: %s", key, exc)
        await _end_work_session(db)
        return ConnectionSyncReport(
            key, progress.rows_confirmed, progress.in_doubt_day, f"{type(exc).__name__}: {exc}")
    return ConnectionSyncReport(key, progress.rows_confirmed)


async def sync_targets(
    db: AsyncSession, *, user_id: int | None = None, provider: str | None = None
) -> list[tuple[int, str]]:
    """``(connection_id, report key)`` for every stored connection, or one user's.

    Plain values, not ORM objects: after a failed attempt the session is rolled back, which
    expires ORM objects, so each attempt reloads its own row from its id.
    """
    stmt = select(WearableConnection.id, WearableConnection.user_id, WearableConnection.provider)
    if user_id is not None:
        stmt = stmt.where(WearableConnection.user_id == user_id)
    if provider is not None:
        stmt = stmt.where(WearableConnection.provider == provider)
    rows = (await db.execute(stmt.order_by(WearableConnection.id))).all()
    return [(cid, f"user{uid}:{prov}") for cid, uid, prov in rows]


async def sync_all(db: AsyncSession, *, days: int = DEFAULT_SYNC_DAYS) -> list[ConnectionSyncReport]:
    """Sync every stored connection, one at a time. One failing does not stop the rest."""
    return [await sync_one(db, cid, key, days=days) for cid, key in await sync_targets(db)]


# --- small helpers ---------------------------------------------------------------


def _utc_today() -> date_cls:
    return datetime.now(UTC).date()


def stored_expiry(expires_at: datetime | None) -> datetime | None:
    """Adapters report expiry as aware UTC; the column is naive UTC (``_aware`` restores it)."""
    return utc_naive(expires_at) if expires_at is not None else None


def _aware(dt: datetime) -> datetime:
    """Treat naive DB timestamps as UTC for comparison/arithmetic."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
