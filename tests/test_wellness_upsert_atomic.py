"""upsert_wellness_sample is atomic and race-safe (PA-10... PA-11).

The upsert used to SELECT then INSERT/UPDATE, so two concurrent writers for the same
(user_id, date, source) — an on-demand sync overlapping the nightly cron, or a client
retry — both missed the SELECT, both INSERTed, and the second tripped the
uq_wellness_user_date_source constraint into an uncaught IntegrityError (a 500). It is
now a single INSERT ... ON CONFLICT DO UPDATE; these tests pin the in-place-update
semantics and that concurrent writers no longer raise.

Requires a live DB (async_db).
"""
import asyncio
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.models.wellness import WellnessSample
from app.schemas.wellness import WellnessSampleIn
from app.services.readiness_service import upsert_wellness_sample

pytestmark = pytest.mark.asyncio


async def _mk_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _rows_for(db: AsyncSession, user_id: int, d: date, source: str) -> list[WellnessSample]:
    return list(
        (
            await db.execute(
                select(WellnessSample).where(
                    WellnessSample.user_id == user_id,
                    WellnessSample.date == d,
                    WellnessSample.source == source,
                )
            )
        ).scalars().all()
    )


async def test_upsert_is_idempotent_and_updates_in_place(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "well_idem@test.com")
    d = date(2026, 6, 25)

    first = await upsert_wellness_sample(
        async_db, user.id, WellnessSampleIn(date=d, source="manual", hrv_ms=70.0, sleep_hours=8.0)
    )
    created_at = first.created_at

    second = await upsert_wellness_sample(
        async_db, user.id, WellnessSampleIn(date=d, source="manual", hrv_ms=55.0, mood=6.0)
    )

    rows = await _rows_for(async_db, user.id, d, "manual")
    assert len(rows) == 1, "the second upsert must update in place, not insert a duplicate"
    assert second.id == first.id
    assert rows[0].hrv_ms == 55.0        # updated
    assert rows[0].mood == 6.0           # updated
    assert rows[0].sleep_hours is None   # full-field replace (unchanged from the old behavior)
    assert rows[0].created_at == created_at  # DO UPDATE touches only signal columns


async def test_concurrent_upserts_same_key_do_not_raise(async_db: AsyncSession) -> None:
    """The race the fix exists for: two writers for one key, concurrently. The old
    SELECT-then-INSERT let the second collide into an IntegrityError; the atomic upsert
    resolves it in the database — no exception, exactly one row."""
    user = await _mk_user(async_db, "well_race@test.com")
    d = date(2026, 6, 24)
    factory = async_sessionmaker(async_db.bind, expire_on_commit=False)

    async def _write(hrv: float) -> None:
        async with factory() as session:
            await upsert_wellness_sample(
                session, user.id, WellnessSampleIn(date=d, source="oura", hrv_ms=hrv)
            )

    # Must not raise a duplicate-key IntegrityError.
    await asyncio.gather(_write(60.0), _write(65.0))

    rows = await _rows_for(async_db, user.id, d, "oura")
    assert len(rows) == 1, "concurrent upserts must converge to one row"
    assert rows[0].hrv_ms in (60.0, 65.0), "one of the two concurrent writers wins"
