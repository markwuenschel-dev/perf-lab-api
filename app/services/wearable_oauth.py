"""The wearable OAuth transaction: a durable attempt, bound to its browser by PKCE (ADR-0075).

``start`` records a ``pending`` attempt and returns the provider's consent URL. The provider
sends the browser back to the SPA (``WEB_APP_URL`` + ``/settings/oura-callback``), which calls
``complete`` with the code, the state and the PKCE verifier it kept:

1. **Check without consuming.** The state must name an attempt of this user and provider, and the
   verifier must hash to its challenge. A failure leaves the row untouched.
2. **Classify.** A finished attempt answers with its own outcome, every time. A claim at least
   ``STALE_CLAIM_AFTER`` old is failed durably first (``claim_stale``). Only a ``pending``
   attempt goes on.
3. **Claim and commit**, before any external call, with an UPDATE that rechecks status and
   expiry against the current database clock. Losing the claim means re-reading and
   classifying, never exchanging.
4. **Exchange** the code, with no transaction open.
5. **Finalize** in one transaction: lock the attempt, recheck that it is still ``claimed`` (a
   duplicate may have failed it as stale meanwhile), then write the connection (``generation +
   1``) and the receipt together. If anything in that transaction fails, before or during
   the commit, it rolls back and no outcome is reported (503 ``outcome_unknown``). Saving a
   failed exchange is guarded the same way.

Time. Each transition first locks its row, then samples ``timezone('UTC', clock_timestamp())``
once (``FROM _NOW``), never ``now()``, which is the transaction's start time, and uses that one
sample both for its guard and for the time it records. Expiry and staleness compare against that raw sample, so their
elapsed-time guarantees depend on the database clock: a clock that steps backward extends them,
and repeated steps can extend them by more than one step's size. The recorded transition times
(``claimed_at``, ``outcome_at``) are ordering timestamps, not exact wall-clock observations: each
is clamped to be no earlier than the times already on the row, so a backward step cannot break
``ck_oauth_authorizations_time_order``. A clamp is logged with the attempt id and its size.

Every re-read is a fresh Core SELECT: the session keeps ORM objects across commits
(``expire_on_commit=False``).

Secrets: only the sha256 of ``state`` is stored; the code and the verifier never are, and no
log line carries them or a provider error body.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.integrations.base import ProviderRejected, TokenBundle, WearableAdapter
from app.services import wearable_service

logger = logging.getLogger("perflab.wearable.oauth")

#: Where the provider returns the browser: an SPA route, so the completion call is authenticated.
CALLBACK_PATH = "/settings/oura-callback"
ATTEMPT_TTL = timedelta(minutes=10)
#: A claim this old (or older) has lost its worker, as far as a duplicate can tell.
STALE_CLAIM_AFTER = timedelta(minutes=2)

_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CLOCK = "timezone('UTC', clock_timestamp())"
#: One clock sample per statement, as ``c.t``: the guard and the recorded time see the same instant.
_NOW = f"(SELECT {_CLOCK} AS t) c"
#: Recorded outcome time: the sample, but never before the times already on the row.
_OUTCOME_AT = "GREATEST(c.t, a.created_at, a.claimed_at)"
#: In RETURNING (new values): how far the recorded time was moved past the sample, in seconds.
_CLAMPED = "EXTRACT(EPOCH FROM ({col} - c.t))::float8 AS clamped"

AttemptRow = Row[Any]


class AttemptRefused(Exception):
    """The request does not reach a classification: ``status_code`` and ``reason`` say why."""

    def __init__(self, status_code: int, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class AttemptResult:
    status: str  # "succeeded" | "failed" | "in_progress"
    reason: str | None = None
    completed_at: datetime | None = None
    connection_id: int | None = None
    generation: int | None = None


class OutcomeUnknown(Exception):
    """The finalizing transaction raised. Reported as unknown even when it failed before the
    commit, so the client always keeps its inputs and retries; the attempt itself says what
    happened."""


# --- pure helpers ------------------------------------------------------------------


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def redirect_uri() -> str:
    """``WEB_APP_URL`` + the callback path, validated. ``OURA_REDIRECT_URI`` is not read: it is
    the pre-P6b release's setting and stays on the box for its rollback."""
    base = settings.WEB_APP_URL.rstrip("/")
    uri = f"{base}{CALLBACK_PATH}"
    parts = urlsplit(uri)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise ValueError(f"WEB_APP_URL does not give a usable OAuth redirect URI: {base!r}")
    if settings.is_production and parts.scheme != "https":  # the canonical predicate, aliases included
        raise ValueError("WEB_APP_URL must be https in production (it is the OAuth redirect URI)")
    return uri


def valid_challenge(challenge: str | None, method: str | None) -> bool:
    return bool(challenge) and _CHALLENGE.fullmatch(challenge or "") is not None and method == "S256"


def _result(row: AttemptRow) -> AttemptResult:
    if row.status == "succeeded":
        return AttemptResult("succeeded", None, row.outcome_at, row.receipt_connection_id,
                             row.receipt_generation)
    if row.status == "failed":
        return AttemptResult("failed", row.error, row.outcome_at)
    return AttemptResult("in_progress")


# --- the transaction ---------------------------------------------------------------


async def start(db: AsyncSession, adapter: WearableAdapter, user_id: int, code_challenge: str) -> str:
    """Record a pending attempt and return the consent URL that carries it."""
    uri = redirect_uri()
    state = secrets.token_urlsafe(32)
    await db.execute(text(  # one clock sample: the lifetime is exactly ATTEMPT_TTL
        "INSERT INTO oauth_authorizations (user_id, provider, state_hash, code_challenge, "
        "redirect_uri, status, created_at, expires_at) SELECT :u, :p, :h, :c, :r, 'pending', "
        f"c.t, c.t + make_interval(secs => :ttl) FROM {_NOW}"
    ), {"u": user_id, "p": adapter.provider, "h": state_hash(state), "c": code_challenge,
        "r": uri, "ttl": ATTEMPT_TTL.total_seconds()})
    url = adapter.build_authorize_url(state, code_challenge=code_challenge, redirect_uri=uri)
    await db.commit()
    return url


async def _read(db: AsyncSession, hashed: str) -> AttemptRow | None:
    row = (await db.execute(text(
        "SELECT id, user_id, provider, code_challenge, redirect_uri, status, claimed_at, "
        "outcome_at, error, receipt_connection_id, receipt_generation "
        "FROM oauth_authorizations WHERE state_hash = :h"
    ), {"h": hashed})).one_or_none()
    await db.commit()  # end the read: the next statement sees whatever committed since
    return row


def _check(row: AttemptRow | None, user_id: int, provider: str, verifier: str) -> AttemptRow:
    if row is None:
        raise AttemptRefused(400, "invalid", "invalid_state")
    if row.user_id != user_id or row.provider != provider:
        raise AttemptRefused(403, "forbidden", "not_your_attempt")
    if not hmac.compare_digest(s256(verifier), row.code_challenge):
        raise AttemptRefused(400, "invalid", "verifier_mismatch")
    return row


def _note_clamp(attempt_id: int, clamped: float | None) -> None:
    if clamped is not None and clamped > 0:
        logger.warning("OAuth attempt %s: database clock was %.3fs behind the attempt's recorded "
                       "times; the transition time was clamped", attempt_id, clamped)


async def _transition(db: AsyncSession, attempt_id: int, sql: str, params: dict[str, Any]) -> bool:
    """Lock the attempt, then run one guarded transition (its RETURNING gives ``clamped``) and
    commit it. True if it moved the row.

    The lock comes first, in its own statement. An UPDATE samples ``_NOW`` before it waits for a
    locked row and rechecks its guard with that old sample afterwards, so a claim waiting behind
    another transaction would judge expiry at a moment already past. Locked first, every guard
    reads the clock with the row in hand."""
    await db.execute(text("SELECT 1 FROM oauth_authorizations WHERE id = :i FOR UPDATE"),
                     {"i": attempt_id})
    row = (await db.execute(text(sql), {"i": attempt_id, **params})).first()
    await db.commit()
    if row is None:
        return False
    _note_clamp(attempt_id, row.clamped)
    return True


async def _fail_if_stale(db: AsyncSession, attempt_id: int) -> bool:
    return await _transition(db, attempt_id, (
        "UPDATE oauth_authorizations a SET status = 'failed', error = 'claim_stale', "
        f"outcome_at = {_OUTCOME_AT} FROM {_NOW} WHERE a.id = :i AND a.status = 'claimed' "
        "AND a.claimed_at <= c.t - make_interval(secs => :stale) "
        f"RETURNING {_CLAMPED.format(col='a.outcome_at')}"
    ), {"stale": STALE_CLAIM_AFTER.total_seconds()})


async def _claim(db: AsyncSession, attempt_id: int) -> bool:
    return await _transition(db, attempt_id, (
        "UPDATE oauth_authorizations a SET status = 'claimed', "
        f"claimed_at = GREATEST(c.t, a.created_at) FROM {_NOW} "
        "WHERE a.id = :i AND a.status = 'pending' AND a.expires_at > c.t "
        f"RETURNING {_CLAMPED.format(col='a.claimed_at')}"
    ), {})


async def _fail_pending(db: AsyncSession, attempt_id: int, reason: str, *, expired_only: bool) -> bool:
    guard = " AND a.expires_at <= c.t" if expired_only else ""
    return await _transition(db, attempt_id, (
        f"UPDATE oauth_authorizations a SET status = 'failed', error = :e, outcome_at = {_OUTCOME_AT} "
        f"FROM {_NOW} WHERE a.id = :i AND a.status = 'pending'{guard} "
        f"RETURNING {_CLAMPED.format(col='a.outcome_at')}"
    ), {"e": reason})


async def _classify(db: AsyncSession, hashed: str, user_id: int, provider: str, verifier: str,
                    *, claim: bool) -> tuple[AttemptResult | None, AttemptRow]:
    """Settle where the attempt stands. Returns (result, row); result None means this caller
    holds a fresh claim (``claim=True`` only) and must exchange."""
    row = _check(await _read(db, hashed), user_id, provider, verifier)
    # Each extra pass follows a transition just made (here or by another request), and every
    # transition is forward-only, so this settles within a few passes.
    for _ in range(4):
        if row.status in ("succeeded", "failed"):
            return _result(row), row
        if row.status == "claimed":
            if not await _fail_if_stale(db, row.id):
                return AttemptResult("in_progress"), row
        elif not claim:
            return AttemptResult("in_progress"), row
        elif await _claim(db, row.id):
            return None, row
        else:
            # Lost the claim, or the attempt expired: settle expiry durably, then look again.
            await _fail_pending(db, row.id, "expired", expired_only=True)
        row = _check(await _read(db, hashed), user_id, provider, verifier)
    return AttemptResult("in_progress"), row


async def complete(db: AsyncSession, adapter: WearableAdapter, user_id: int, *, state: str,
                   code: str, code_verifier: str) -> AttemptResult:
    hashed = state_hash(state)
    settled, row = await _classify(db, hashed, user_id, adapter.provider, code_verifier, claim=True)
    if settled is not None:
        return settled

    try:
        tokens = await adapter.exchange_code(code, code_verifier=code_verifier,
                                             redirect_uri=row.redirect_uri)
        sealed = _seal(tokens)
    except Exception as exc:  # noqa: BLE001 — every failure is an outcome; nothing is re-raised
        reason = "exchange_rejected" if _is_rejection(exc) else "exchange_failed"
        # The class name only: an exception's text can carry a provider body or a URL.
        logger.warning("OAuth exchange failed for attempt %s: %s (%s)", row.id,
                       type(exc).__name__, reason)
        await _finalize_failure(db, row.id, reason)
        settled, _ = await _classify(db, hashed, user_id, adapter.provider, code_verifier,
                                     claim=False)
        return settled  # type: ignore[return-value]

    return await _finalize_success(db, row.id, user_id, adapter.provider, sealed, hashed,
                                   code_verifier)


async def cancel(db: AsyncSession, provider: str, user_id: int, *, state: str,
                 code_verifier: str) -> AttemptResult:
    """The athlete declined at the provider: a pending attempt fails as ``cancelled``. Anything
    else is reported as it stands; a claimed or finished attempt is never changed."""
    hashed = state_hash(state)
    row = _check(await _read(db, hashed), user_id, provider, code_verifier)
    if row.status == "pending":
        await _fail_pending(db, row.id, "cancelled", expired_only=False)
    settled, _ = await _classify(db, hashed, user_id, provider, code_verifier, claim=False)
    return settled  # type: ignore[return-value]


# --- finalization ------------------------------------------------------------------


@dataclass(frozen=True)
class _Sealed:
    access_enc: str
    refresh_enc: str | None
    expires_at: datetime | None
    scope: str | None


def _seal(tokens: TokenBundle) -> _Sealed:
    return _Sealed(
        crypto.encrypt(tokens.access_token),
        crypto.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
        wearable_service.stored_expiry(tokens.expires_at),
        tokens.scope,
    )


def _is_rejection(exc: Exception) -> bool:
    """The provider answered and said no. Adapters translate their own refusals into
    ``ProviderRejected``; this service never names a provider (ADR-0044)."""
    return isinstance(exc, ProviderRejected)


async def _outcome_unknown(db: AsyncSession, attempt_id: int, what: str, exc: Exception) -> OutcomeUnknown:
    """Roll back after a failure inside a finalizing transaction; the caller raises the result."""
    logger.error("OAuth attempt %s: %s raised %s; outcome unknown", attempt_id, what,
                 type(exc).__name__)
    try:
        await db.rollback()
    except Exception:  # noqa: BLE001 — the connection may be gone; nothing more to undo
        pass
    return OutcomeUnknown()


async def _finalize_failure(db: AsyncSession, attempt_id: int, reason: str) -> None:
    """Record why the exchange failed. Inside the uncertainty boundary like a success: the code is
    spent either way, so a failure to save the outcome is reported as no outcome."""
    try:
        await _transition(db, attempt_id, (
            f"UPDATE oauth_authorizations a SET status = 'failed', error = :e, outcome_at = {_OUTCOME_AT} "
            f"FROM {_NOW} WHERE a.id = :i AND a.status = 'claimed' "
            f"RETURNING {_CLAMPED.format(col='a.outcome_at')}"
        ), {"e": reason})
    except Exception as exc:
        raise await _outcome_unknown(db, attempt_id, "saving the exchange failure", exc) from None


async def _finalize_success(db: AsyncSession, attempt_id: int, user_id: int, provider: str,
                            sealed: _Sealed, hashed: str, verifier: str) -> AttemptResult:
    # The whole transaction is the uncertainty boundary: the lock, the connection write, the
    # receipt and the commit. Any failure in it rolls back and is reported as no outcome.
    linked: tuple[AttemptResult, float | None] | None = None  # None: fenced by a duplicate
    try:
        still_claimed = (await db.execute(text(
            "SELECT status FROM oauth_authorizations WHERE id = :i FOR UPDATE"
        ), {"i": attempt_id})).scalar_one() == "claimed"
        if still_claimed:
            connection_id, generation = await wearable_service.upsert_connection(
                db, user_id, provider=provider, auth_type="oauth", access_enc=sealed.access_enc,
                refresh_enc=sealed.refresh_enc, expires_at=sealed.expires_at, scope=sealed.scope,
            )
            receipt = (await db.execute(text(
                f"UPDATE oauth_authorizations a SET status = 'succeeded', outcome_at = {_OUTCOME_AT}, "
                "receipt_connection_id = :c, receipt_generation = :g, connection_id = :c "
                f"FROM {_NOW} WHERE a.id = :i AND a.status = 'claimed' "
                f"RETURNING a.outcome_at, {_CLAMPED.format(col='a.outcome_at')}"
            ), {"i": attempt_id, "c": connection_id, "g": generation})).one()
            await db.commit()
            linked = (AttemptResult("succeeded", None, receipt.outcome_at, connection_id, generation),
                      receipt.clamped)
        else:
            await db.rollback()
    except Exception as exc:
        raise await _outcome_unknown(db, attempt_id, "the finalizing transaction", exc) from None

    if linked is None:
        # A duplicate failed this claim as stale while the exchange ran. Its outcome stands;
        # the tokens just obtained are dropped, never stored.
        logger.warning("OAuth attempt %s was failed while exchanging; tokens discarded", attempt_id)
        settled, _ = await _classify(db, hashed, user_id, provider, verifier, claim=False)
        return settled  # type: ignore[return-value]
    result, clamped = linked
    _note_clamp(attempt_id, clamped)
    return result
