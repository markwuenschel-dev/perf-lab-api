"""Phone-pushed wellness: personal ingest tokens and the ingest itself.

A token is created by the signed-in athlete (Settings), shown once, and stored only as a
SHA-256. It authenticates exactly one call, ``POST /v1/wellness/ingest``, which writes one
day's device readings through the canonical wellness sink (``upsert_wellness_sample``,
idempotent on athlete + date + source). A successful ingest stamps ``last_used_at``, the
athlete's "last successful Apple sync".
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.prescription_evidence import utc_naive
from app.models.wellness_ingest_token import WellnessIngestToken
from app.schemas.wellness import WellnessIngestIn, WellnessSampleIn, WellnessSampleOut
from app.services import readiness_service

#: Distinguishes an ingest token from a login JWT at a glance (and in a leaked-secret scan).
TOKEN_PREFIX = "plw_"
#: Shown in Settings so the athlete can tell tokens apart; never enough to use one.
_DISPLAY_CHARS = 8


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return utc_naive(datetime.now(UTC))


async def create_token(
    db: AsyncSession, user_id: int, label: str
) -> tuple[WellnessIngestToken, str]:
    """A new token for ``user_id``. Returns the row and the plaintext, which is not stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = WellnessIngestToken(
        user_id=user_id,
        token_hash=_hash(token),
        token_prefix=token[: len(TOKEN_PREFIX) + _DISPLAY_CHARS],
        label=label.strip(),
        created_at=_now(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, token


async def list_tokens(db: AsyncSession, user_id: int) -> list[WellnessIngestToken]:
    """The athlete's active (unrevoked) tokens, newest first."""
    rows = await db.execute(
        select(WellnessIngestToken)
        .where(WellnessIngestToken.user_id == user_id, WellnessIngestToken.revoked_at.is_(None))
        .order_by(WellnessIngestToken.created_at.desc(), WellnessIngestToken.id.desc())
    )
    return list(rows.scalars().all())


async def revoke_token(db: AsyncSession, user_id: int, token_id: int) -> bool:
    """Revoke one of the athlete's tokens. False when it is not theirs or already revoked."""
    row = (
        await db.execute(
            select(WellnessIngestToken).where(
                WellnessIngestToken.id == token_id,
                WellnessIngestToken.user_id == user_id,
                WellnessIngestToken.revoked_at.is_(None),
            )
        )
    ).scalars().first()
    if row is None:
        return False
    row.revoked_at = _now()
    await db.commit()
    return True


async def authenticate(db: AsyncSession, token: str) -> WellnessIngestToken | None:
    """The active token row for a presented token, or None."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    return (
        await db.execute(
            select(WellnessIngestToken).where(
                WellnessIngestToken.token_hash == _hash(token),
                WellnessIngestToken.revoked_at.is_(None),
            )
        )
    ).scalars().first()


async def ingest(
    db: AsyncSession, token: WellnessIngestToken, body: WellnessIngestIn
) -> WellnessSampleOut:
    """Write one day's pushed readings for the token's athlete, then mark the token used."""
    measured_at = utc_naive(body.measured_at) if body.measured_at is not None else None
    day = body.date or (measured_at or _now()).date()
    user_id, token_id = token.user_id, token.id
    sample = await readiness_service.upsert_wellness_sample(
        db,
        user_id,
        WellnessSampleIn(
            date=day,
            source=body.source,
            hrv_ms=body.hrv_ms,
            hrv_metric=body.hrv_metric if body.hrv_ms is not None else None,
            resting_hr=body.resting_hr,
            sleep_hours=body.sleep_hours,
            measured_at=measured_at,
            raw={"via": "wellness_ingest", "token_id": token_id},
        ),
    )
    # Materialize before the next commit expires the row (async lazy-loads would fail).
    out = WellnessSampleOut.model_validate(sample)
    token.last_used_at = _now()
    await db.commit()
    return out
