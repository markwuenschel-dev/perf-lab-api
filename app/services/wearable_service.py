"""Wearable-sync orchestration — provider-independent (OAuth + PAT).

Ties the provider adapter, the encrypted token store (``WearableConnection``), and
the wellness sink together:

- OAuth: ``get_authorize_url`` → user consents → ``handle_oauth_callback`` stores an
  encrypted connection. Identity rides a short-lived signed ``state`` JWT because the
  browser redirect carries no Bearer header.
- PAT: ``connect_pat`` validates a Personal Access Token and stores it.
- Sync: ``sync_connection`` refreshes the OAuth token as needed, pulls new days, and
  upserts them via the canonical ``readiness_service.upsert_wellness_sample`` sink
  (source = the connection's own provider slug), advancing ``last_sync_at``. ``sync_all`` drives every connection
  (used by the nightly cron job).

Tokens are Fernet-encrypted at rest (``app.core.crypto``); this module is the only
place that decrypts them.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.integrations.base import TokenBundle, WearableAdapter
from app.integrations.registry import adapter_for
from app.logic.prescription_evidence import utc_naive
from app.models.wearable_connection import WearableConnection
from app.schemas.wellness import WellnessSampleIn
from app.services import readiness_service

logger = logging.getLogger("perflab.wearable")

# OAuth ``state`` token: a short-lived signed JWT carrying the user id through the
# browser redirect. Reuses the app's JWT secret; a distinct purpose claim prevents
# it from being confused with an access token.
# Provider-neutral: a Garmin consent flow signing a token whose purpose claims "oura_oauth"
# would be a small lie of exactly the kind this module is being cleaned of. Changing the value
# invalidates state tokens already in flight, which is bounded by the 10-minute TTL below —
# an athlete mid-consent at deploy time sees an error and reconnects.
_STATE_PURPOSE = "wearable_oauth"
_STATE_TTL = timedelta(minutes=10)

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


# --- OAuth state token (pure; unit-tested directly) ------------------------------


def sign_state(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": _STATE_PURPOSE,
        "exp": datetime.now(UTC) + _STATE_TTL,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_authorize_url(user_id: int, provider: str = DEFAULT_PROVIDER) -> str:
    """Build the provider's OAuth authorize URL with a signed state for ``user_id``."""
    return _adapter(provider).build_authorize_url(sign_state(user_id))


def verify_state(token: str) -> int:
    """Return the user id from a valid state token, else raise ValueError."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid or expired OAuth state token") from exc
    if payload.get("purpose") != _STATE_PURPOSE:
        raise ValueError("OAuth state token has the wrong purpose")
    sub = payload.get("sub")
    if sub is None:
        raise ValueError("OAuth state token missing subject")
    return int(sub)


# --- Connection persistence ------------------------------------------------------


async def get_connection(
    db: AsyncSession, user_id: int, provider: str = DEFAULT_PROVIDER
) -> WearableConnection | None:
    return (
        await db.execute(
            select(WearableConnection).where(
                WearableConnection.user_id == user_id,
                WearableConnection.provider == provider,
            )
        )
    ).scalars().first()


async def _store_connection(
    db: AsyncSession,
    user_id: int,
    *,
    provider: str,
    auth_type: str,
    tokens: TokenBundle,
) -> WearableConnection:
    """Upsert the (encrypted) connection for (user, provider)."""
    conn = await get_connection(db, user_id, provider)
    access_enc = crypto.encrypt(tokens.access_token)
    refresh_enc = crypto.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    if conn is None:
        conn = WearableConnection(user_id=user_id, provider=provider)
        db.add(conn)
    conn.auth_type = auth_type
    conn.access_token_enc = access_enc
    conn.refresh_token_enc = refresh_enc
    conn.expires_at = _stored_expiry(tokens.expires_at)
    conn.scope = tokens.scope
    await db.commit()
    await db.refresh(conn)
    return conn


async def handle_oauth_callback(
    db: AsyncSession, code: str, state: str, provider: str = DEFAULT_PROVIDER
) -> WearableConnection:
    user_id = verify_state(state)
    tokens = await _adapter(provider).exchange_code(code)
    return await _store_connection(
        db, user_id, provider=provider, auth_type="oauth", tokens=tokens
    )


async def connect_pat(
    db: AsyncSession, user_id: int, token: str, provider: str = DEFAULT_PROVIDER
) -> WearableConnection:
    """Validate a Personal Access Token with a live probe, then store it."""
    adapter = _adapter(provider)
    today = _utc_today()
    # A cheap probe that also confirms the token has the right scope.
    await adapter.fetch_daily_wellness(token, today - timedelta(days=1), today)
    return await _store_connection(
        db,
        user_id,
        provider=provider,
        auth_type="pat",
        tokens=TokenBundle(access_token=token),
    )


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
    tokens = await _adapter(conn.provider).refresh_tokens(
        crypto.decrypt(conn.refresh_token_enc)
    )
    conn.access_token_enc = crypto.encrypt(tokens.access_token)
    if tokens.refresh_token:
        conn.refresh_token_enc = crypto.encrypt(tokens.refresh_token)
    conn.expires_at = _stored_expiry(tokens.expires_at)
    if tokens.scope:
        conn.scope = tokens.scope
    await db.commit()
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
        await readiness_service.upsert_wellness_sample(db, conn.user_id, payload)
        written += 1

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


def _stored_expiry(expires_at: datetime | None) -> datetime | None:
    """Adapters report expiry as aware UTC; the column is naive UTC (``_aware`` restores it)."""
    return utc_naive(expires_at) if expires_at is not None else None


def _aware(dt: datetime) -> datetime:
    """Treat naive DB timestamps as UTC for comparison/arithmetic."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
