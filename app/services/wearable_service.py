"""Wearable-sync orchestration — provider-independent (OAuth + PAT).

Ties the provider adapter, the encrypted token store (``WearableConnection``), and
the wellness sink together:

- OAuth: an authorization is a durable attempt (``app.services.wearable_oauth``, ADR-0075);
  its success writes the connection through ``upsert_connection`` here.
- PAT: ``connect_pat`` validates a Personal Access Token and stores it the same way.
- Every successful link advances the row's ``generation``; a token refresh never does, and
  writes only while the generation it read is unchanged, so a refresh that raced a reconnect
  cannot overwrite the new credentials.
- Sync: ``sync_connection`` refreshes the OAuth token as needed, pulls new days, and
  upserts them via the canonical ``readiness_service.upsert_wellness_sample`` sink
  (source = the connection's own provider slug), advancing ``last_sync_at``. ``sync_all`` drives every connection
  (used by the nightly cron job).

Tokens are Fernet-encrypted at rest (``app.core.crypto``); this module and
``wearable_oauth`` are the only places that handle them.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.integrations.base import WearableAdapter
from app.integrations.registry import adapter_for
from app.logic.prescription_evidence import utc_naive
from app.models.wearable_connection import WearableConnection
from app.schemas.wellness import WellnessSampleIn
from app.services import readiness_service
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


async def _valid_access_token(db: AsyncSession, conn: WearableConnection) -> str:
    """Return a usable access token, refreshing an expiring OAuth token in place."""
    access = crypto.decrypt(conn.access_token_enc)
    if conn.auth_type != "oauth":
        return access  # PATs don't expire / can't refresh
    if conn.expires_at is None:
        return access
    if _aware(conn.expires_at) - datetime.now(UTC) > _REFRESH_BUFFER:
        return access
    if not conn.refresh_token_enc:
        logger.warning("Oura connection %s expired with no refresh token", conn.id)
        return access
    # Refresh from what is stored now, and write back only if no link replaced it meanwhile:
    # an OAuth reconnect or a PAT advances the generation, a refresh never does.
    current = (await db.execute(text(
        "SELECT generation, refresh_token_enc FROM wearable_connections WHERE id = :i"
    ), {"i": conn.id})).one_or_none()
    await db.commit()
    if current is None or not current.refresh_token_enc:
        raise ConnectionReplaced(f"connection {conn.id} changed before its refresh")
    tokens = await _adapter(conn.provider).refresh_tokens(
        crypto.decrypt(current.refresh_token_enc)
    )
    written = (await db.execute(text(
        "UPDATE wearable_connections SET access_token_enc = :acc, "
        "refresh_token_enc = COALESCE(:ref, refresh_token_enc), expires_at = :exp, "
        "scope = COALESCE(:scope, scope), updated_at = :now "
        "WHERE id = :i AND generation = :g RETURNING id"
    ), {
        "i": conn.id, "g": current.generation,
        "acc": crypto.encrypt(tokens.access_token),
        "ref": crypto.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
        "exp": stored_expiry(tokens.expires_at), "scope": tokens.scope or None,
        "now": datetime.now(UTC).replace(tzinfo=None),
    })).first()
    await db.commit()
    if written is None:
        raise ConnectionReplaced(f"connection {conn.id} was relinked during its refresh")
    return tokens.access_token


async def sync_connection(
    db: AsyncSession, conn: WearableConnection, *, days: int = DEFAULT_SYNC_DAYS
) -> int:
    """Pull new days for one connection into WellnessSample. Returns rows written."""
    access = await _valid_access_token(db, conn)
    end = _utc_today()
    # Start from the day before the last sync (to catch late-arriving data), else a
    # trailing window on first run.
    if conn.last_sync_at is not None:
        start = _aware(conn.last_sync_at).date() - timedelta(days=1)
    else:
        start = end - timedelta(days=days)
    if start > end:
        start = end

    readings = await _adapter(conn.provider).fetch_daily_wellness(access, start, end)
    written = 0
    for r in readings:
        payload = WellnessSampleIn(
            date=r.day,
            source=conn.provider,
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
        sample = await readiness_service.upsert_wellness_sample(db, conn.user_id, payload)
        written += 1
        # That day is committed: run its shadows now, before the next day's write (P7). Oura
        # supplies no soreness, so the EKF records an explicit skip.
        await run_wellness_shadows(db, sample, assimilate_soreness=False)

    conn.last_sync_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    return written


async def sync_user(
    db: AsyncSession, user_id: int, *, provider: str = DEFAULT_PROVIDER, days: int = DEFAULT_SYNC_DAYS
) -> int:
    conn = await get_connection(db, user_id, provider)
    if conn is None:
        return 0
    return await sync_connection(db, conn, days=days)


async def sync_all(db: AsyncSession, *, days: int = DEFAULT_SYNC_DAYS) -> dict[str, int]:
    """Sync every stored connection. Returns per-connection row counts + a total.

    One connection failing (revoked token, provider outage) does not abort the run.
    """
    conns = (await db.execute(select(WearableConnection))).scalars().all()
    results: dict[str, int] = {}
    total = 0
    for conn in conns:
        key = f"user{conn.user_id}:{conn.provider}"
        try:
            n = await sync_connection(db, conn, days=days)
            results[key] = n
            total += n
        except Exception as exc:  # noqa: BLE001 — isolate per-connection failures
            logger.exception("Wearable sync failed for %s: %s", key, exc)
            results[key] = -1
    results["_total"] = total
    return results


# --- small helpers ---------------------------------------------------------------


def _utc_today() -> date_cls:
    return datetime.now(UTC).date()


def stored_expiry(expires_at: datetime | None) -> datetime | None:
    """Adapters report expiry as aware UTC; the column is naive UTC (``_aware`` restores it)."""
    return utc_naive(expires_at) if expires_at is not None else None


def _aware(dt: datetime) -> datetime:
    """Treat naive DB timestamps as UTC for comparison/arithmetic."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
