"""AUD-C8 replay migration a038 — downgrade/re-upgrade against real lineage rows.

The downgrade recreates the pre-replay ``(source, model)`` unique index, which cannot coexist
with replay rows (they share ``(source, model)`` with their original). a038 resolves this by
deleting the shadow-only replay rows on downgrade. This proves the round-trip is safe against a
database that already holds benchmark updates, wellness updates at multiple correction
revisions, and replay rows — and that ordinary rows survive it.
"""
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from conftest import _TRUNCATE_ALL, TEST_DATABASE_URL
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from alembic import command
from app.logic.ekf.wellness_input import WellnessMeasurement, wellness_content_hash
from app.models.ekf_shadow import EkfShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample

pytestmark = pytest.mark.asyncio

_A037 = "a037_ekf_wellness_idempotency"
_H = wellness_content_hash(WellnessMeasurement(4.0))
_A038_COLUMNS = {"correction_revision", "replayed_revision", "replayed_at",
                 "replayed_by_log_id", "supersedes_log_id", "replay_base_log_id"}


def _alembic(sync_conn: Connection, direction: str, rev: str) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = sync_conn
    (command.downgrade if direction == "down" else command.upgrade)(cfg, rev)


def _column_names(sync_conn: Connection) -> set[str]:
    return {c["name"] for c in inspect(sync_conn).get_columns("ekf_shadow_log")}


def _index_names(sync_conn: Connection) -> set[str]:
    return {i["name"] for i in inspect(sync_conn).get_indexes("ekf_shadow_log")}


@pytest_asyncio.fixture(loop_scope="function")
async def engine(_migrated_schema: None):
    eng = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield eng
    # Always leave the shared schema at head, even if an assertion failed mid-round-trip.
    async with eng.connect() as conn:
        await conn.run_sync(lambda c: _alembic(c, "up", "head"))
    await eng.dispose()


async def _seed_lineage(engine) -> None:
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with Session() as db:
        user = User(email="mig@test.com", hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        sample = WellnessSample(user_id=user.id, date=date(2026, 1, 1), source="manual", soreness=4.0)
        db.add(sample)
        # A benchmark update (source NULL) and a predict row — must survive the round-trip.
        db.add(EkfShadowLog(user_id=user.id, belief_at=now, model_version="ekf-v1", event_type="predict"))
        db.add(EkfShadowLog(user_id=user.id, belief_at=now, model_version="ekf-v1", event_type="update",
                            benchmark_code="1rm"))
        await db.commit()
        await db.refresh(sample)
        # Original wellness assimilation at correction generation 2 (multi-revision).
        original = EkfShadowLog(
            user_id=user.id, belief_at=now, model_version="ekf-v1", event_type="update",
            source_wellness_sample_id=sample.id, assimilated_content_hash=_H, latest_seen_content_hash=_H,
            correction_revision=2, replayed_revision=2,
        )
        db.add(original)
        await db.commit()
        await db.refresh(original)
        for rev in (1, 2):  # two replay generations
            db.add(EkfShadowLog(
                user_id=user.id, belief_at=now, model_version="ekf-v1", event_type="replay",
                source_wellness_sample_id=sample.id, assimilated_content_hash=_H, latest_seen_content_hash=_H,
                correction_revision=rev, supersedes_log_id=original.id, replay_base_log_id=original.id,
            ))
        await db.commit()


async def _count(engine, clause) -> int:
    async with engine.connect() as conn:
        return int(await conn.scalar(select(sa.func.count()).select_from(EkfShadowLog).where(clause)) or 0)


async def test_downgrade_deletes_replays_and_reupgrade_restores(engine):
    await _seed_lineage(engine)
    assert await _count(engine, EkfShadowLog.event_type == "replay") == 2

    # Downgrade to a037: replay rows deleted, a038 columns + indexes gone, pre-replay index back.
    async with engine.connect() as conn:
        await conn.run_sync(lambda c: _alembic(c, "down", _A037))
        cols = await conn.run_sync(_column_names)
        idxs = await conn.run_sync(_index_names)
    assert _A038_COLUMNS.isdisjoint(cols), f"a038 columns survived downgrade: {_A038_COLUMNS & cols}"
    assert "uq_ekf_shadow_wellness_sample_model" in idxs      # pre-replay unique index restored
    assert "uq_ekf_wellness_replay_revision" not in idxs

    assert await _count(engine, EkfShadowLog.event_type == "replay") == 0          # replays removed
    assert await _count(engine, EkfShadowLog.event_type == "predict") == 1         # predict survives
    assert await _count(engine, EkfShadowLog.source_wellness_sample_id.isnot(None)) == 1  # 1 wellness update
    assert await _count(engine, EkfShadowLog.benchmark_code == "1rm") == 1         # benchmark survives

    # Re-upgrade to head: the a038 columns and replay index return (round-trip is clean).
    async with engine.connect() as conn:
        await conn.run_sync(lambda c: _alembic(c, "up", "head"))
        cols = await conn.run_sync(_column_names)
        idxs = await conn.run_sync(_index_names)
    assert _A038_COLUMNS <= cols
    assert {"uq_ekf_original_wellness_source_model", "uq_ekf_wellness_replay_revision"} <= idxs


async def test_a037_pending_rows_are_revision_backfilled_and_flag_invariant_enforced(engine):
    # Start at the shipped C8 schema, then seed the two possible original-row states it can contain.
    async with engine.connect() as conn:
        await conn.run_sync(lambda c: _alembic(c, "down", _A037))

    now = datetime.now(UTC).replace(tzinfo=None)
    async with engine.begin() as conn:
        user_id = int(
            (
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO users (email, hashed_password, is_active, created_at)
                        VALUES (:email, 'x', true, :now)
                        RETURNING id
                        """
                    ),
                    {"email": "a037-backfill@test.com", "now": now},
                )
            ).scalar_one()
        )
        pending_sample_id = int(
            (
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO wellness_samples
                            (user_id, date, source, soreness, created_at)
                        VALUES (:uid, :day, 'manual', 8.0, :now)
                        RETURNING id
                        """
                    ),
                    {"uid": user_id, "day": date(2026, 7, 1), "now": now},
                )
            ).scalar_one()
        )
        clean_sample_id = int(
            (
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO wellness_samples
                            (user_id, date, source, soreness, created_at)
                        VALUES (:uid, :day, 'manual', 4.0, :now)
                        RETURNING id
                        """
                    ),
                    {"uid": user_id, "day": date(2026, 7, 2), "now": now},
                )
            ).scalar_one()
        )
        h8 = wellness_content_hash(WellnessMeasurement(8.0))
        for source_id, assimilated_hash, latest_hash, pending in (
            (pending_sample_id, _H, h8, True),
            (clean_sample_id, _H, _H, False),
        ):
            await conn.execute(
                sa.text(
                    """
                    INSERT INTO ekf_shadow_log (
                        user_id, created_at, belief_at, model_version, event_type,
                        mean_json, variance_json, covariance_json, decision_impact,
                        source_wellness_sample_id, assimilated_content_hash,
                        latest_seen_content_hash, correction_requires_replay,
                        correction_detected_at
                    ) VALUES (
                        :uid, :now, :now, 'ekf-v1', 'update',
                        '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'none_shadow_only',
                        :source_id, :assimilated_hash, :latest_hash, :pending, :detected_at
                    )
                    """
                ),
                {
                    "uid": user_id,
                    "now": now,
                    "source_id": source_id,
                    "assimilated_hash": assimilated_hash,
                    "latest_hash": latest_hash,
                    "pending": pending,
                    "detected_at": now if pending else None,
                },
            )

    async with engine.connect() as conn:
        await conn.run_sync(lambda c: _alembic(c, "up", "head"))

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        pending_row = (
            await db.execute(
                select(EkfShadowLog).where(
                    EkfShadowLog.source_wellness_sample_id == pending_sample_id
                )
            )
        ).scalar_one()
        clean_row = (
            await db.execute(
                select(EkfShadowLog).where(
                    EkfShadowLog.source_wellness_sample_id == clean_sample_id
                )
            )
        ).scalar_one()

    assert pending_row.event_type == "update"
    assert pending_row.correction_revision == 1
    assert pending_row.replayed_revision == 0
    assert pending_row.correction_requires_replay is True
    assert clean_row.event_type == "update"
    assert clean_row.correction_revision == 0
    assert clean_row.replayed_revision == 0
    assert clean_row.correction_requires_replay is False

    # The database, not only service code, enforces both directions of the sticky flag invariant.
    with pytest.raises(sa.exc.IntegrityError):
        async with Session() as db:
            await db.execute(
                sa.update(EkfShadowLog)
                .where(EkfShadowLog.id == clean_row.id)
                .values(correction_requires_replay=True)
            )
            await db.commit()

    with pytest.raises(sa.exc.IntegrityError):
        async with Session() as db:
            await db.execute(
                sa.update(EkfShadowLog)
                .where(EkfShadowLog.id == pending_row.id)
                .values(correction_requires_replay=False)
            )
            await db.commit()
