"""AUD-C24: the shadow-input cascade regression — the load-bearing test.

An early best-effort shadow that does real DB work and rolls back (expiring the shared
WellnessSample) must NOT starve the later shadows: a later writer must still receive the
original finalized values (via the immutable snapshot) and durably persist its row, and the
live wellness write must survive.

Exercised at the SERVICE level over one shared request-session (the production shape:
``get_db`` yields a single session per request, shared across the shadow chain) rather than
through the ASGI/httpx client. A route-level version tripped a NullPool+ASGI reconnect artifact
during the injected rollback; a pooled-vs-NullPool diagnostic proved the shared session is
*usable* after an early best-effort rollback on both pools, so the cascade is a data-handoff
defect (fixed by the snapshot), not a session-poisoning one — no session isolation needed.

Red-capable: pass the live ``sample`` to the later shadow instead of the snapshot and the
early rollback expires it, the later read raises MissingGreenlet, and its row is dropped.
"""
from datetime import date

import pytest
import pytest_asyncio
import sqlalchemy as sa
from conftest import _TRUNCATE_ALL, TEST_DATABASE_URL
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services.personalization_shadow_service import record_personalization_shadow
from app.services.telemetry_common import best_effort_write

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(loop_scope="function")
async def factory(_migrated_schema: None):
    # A POOLED engine (not NullPool) — matches production, so an early best-effort rollback
    # returns a clean connection to the pool instead of the NullPool dispose-and-reconnect
    # that a shared async test session mishandles (a harness artifact, not a real defect).
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _fail_early_shadow(db: AsyncSession) -> None:
    """An early best-effort shadow that does DB work then fails — rolls back (expiring the
    shared sample) and swallows, exactly as a real writer would."""
    async with best_effort_write(db, "test-injected early shadow failure"):
        await db.execute(text("SELECT 1"))
        raise RuntimeError("injected early shadow failure")


async def test_early_shadow_rollback_does_not_starve_a_later_shadow(factory):
    async with factory() as db:  # one shared session for the whole "request"
        user = User(email="c24_cascade@test.com", hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        sample = WellnessSample(
            user_id=user.id, date=date(2026, 1, 1), source="manual",
            sleep_hours=7.5, hrv_ms=61.0, resting_hr=52.0, soreness=4.0, mood=8.0,
        )
        db.add(sample)
        await db.commit()
        uid = user.id  # capture BEFORE the rollback expires the `user` ORM instance

        # Snapshot the later shadow's input from the valid sample, before the chain runs.
        snapshot = WellnessTelemetrySnapshot.from_sample(sample)

        # Early shadow fails and rolls back -> the shared `sample` (and `user`) are now expired.
        await _fail_early_shadow(db)

        # The later shadow (personalization) uses the immutable snapshot + the captured id.
        await record_personalization_shadow(db, uid, snapshot)

    # Fresh session (never made the writes): the later shadow's row is durable with the
    # ORIGINAL values, and the live wellness write survived.
    async with factory() as db:
        prows = (await db.execute(select(PersonalizationShadowLog).where(
            PersonalizationShadowLog.user_id == uid
        ))).scalars().all()
        assert len(prows) == 1
        w = prows[0].wellness
        assert w["sleep_hours"] == 7.5 and w["hrv_ms"] == 61.0 and w["soreness"] == 4.0
        assert (await db.scalar(
            select(func.count()).select_from(WellnessSample).where(WellnessSample.user_id == uid)
        )) == 1
