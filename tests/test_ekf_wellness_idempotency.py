"""AUD-C8: database-backed idempotency for the shadow-EKF wellness assimilation.

The shadow EKF used to re-assimilate a wellness reading on every re-POST (an idempotent
wellness upsert on client retry shrank variance again — overconfidence that corrupts the
evidence a future promotion would rest on). These tests pin the fix:

- exact retries do not re-assimilate (the partial unique index is the concurrency authority);
- a correction (same identity, changed content) does not sequentially re-assimilate — it is
  marked ``correction_requires_replay`` (sticky) with the assimilated vs latest hashes kept
  distinct;
- the claim + belief update commit atomically in the shadow transaction, and a shadow failure
  never touches the live wellness write;
- a linked row must carry complete hash provenance (DB CHECK).
"""
import asyncio
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from conftest import _TRUNCATE_ALL, TEST_DATABASE_URL
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.logic.ekf.wellness_input import (
    HASH_POLICY_VERSION,
    WellnessMeasurement,
    build_wellness_shadow_input,
    wellness_content_hash,
)
from app.models.ekf_shadow import EkfShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services import ekf_shadow_service
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio

_D = date(2026, 1, 1)


@pytest_asyncio.fixture(loop_scope="function")
async def factory(_migrated_schema: None):
    """A session factory (opens/closes sessions per operation, like production)."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _seed(factory, email="c8@test.com") -> int:
    """User + baseline athlete state (the EKF needs a prior state to seed the belief)."""
    async with factory() as db:
        user = User(email=email, hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await initialize_athlete_state(db, user.id)
        await db.commit()
        return user.id


async def _mk_wellness(factory, user_id: int, soreness: float, d: date = _D) -> int:
    async with factory() as db:
        w = WellnessSample(user_id=user_id, date=d, source="manual", soreness=soreness)
        db.add(w)
        await db.commit()
        await db.refresh(w)
        return w.id


async def _set_soreness(factory, sample_id: int, soreness: float | None) -> None:
    """Correct the durable wellness sample (the production upsert does this before the shadow)."""
    async with factory() as db:
        await db.execute(
            sa.update(WellnessSample).where(WellnessSample.id == sample_id).values(soreness=soreness)
        )
        await db.commit()


async def _assimilate(factory, user_id: int, sample_id: int, soreness: float) -> str:
    """Run the shadow-EKF wellness assimilation in its own session; return the outcome."""
    async with factory() as db:
        si = build_wellness_shadow_input(user_id, sample_id, soreness)
        return await ekf_shadow_service.record_ekf_wellness_observation(
            db, user_id, si, observed_at=datetime.now(UTC)
        )


async def _wellness_rows(factory, user_id: int) -> list[EkfShadowLog]:
    """EKF rows linked to a wellness observation, read through a fresh session."""
    async with factory() as db:
        res = await db.execute(
            select(EkfShadowLog)
            .where(
                EkfShadowLog.user_id == user_id,
                EkfShadowLog.source_wellness_sample_id.isnot(None),
            )
            .order_by(EkfShadowLog.id)
        )
        return list(res.scalars().all())


# ── hash unit tests ────────────────────────────────────────────────────────────────────

def test_hash_is_deterministic_and_soreness_sensitive():
    h3a = wellness_content_hash(WellnessMeasurement(soreness=3.0))
    h3b = wellness_content_hash(WellnessMeasurement(soreness=3.0))
    h5 = wellness_content_hash(WellnessMeasurement(soreness=5.0))
    hn = wellness_content_hash(WellnessMeasurement(soreness=None))
    assert h3a == h3b  # deterministic
    assert h3a != h5  # a changed soreness changes the hash
    assert hn != h3a and len(h3a) == 64  # None is a distinct, stable, 64-hex value


def test_hash_pins_canonical_serialization_and_policy_version():
    import hashlib
    import json

    payload = {"policy": HASH_POLICY_VERSION, "soreness": 3.0}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert wellness_content_hash(WellnessMeasurement(soreness=3.0)) == expected


def test_build_input_only_carries_soreness():
    si = build_wellness_shadow_input(user_id=1, wellness_sample_id=2, soreness=4.0)
    assert si.measurement == WellnessMeasurement(soreness=4.0)
    assert si.content_hash == wellness_content_hash(WellnessMeasurement(soreness=4.0))


# ── idempotency semantics ──────────────────────────────────────────────────────────────

async def test_exact_retry_assimilates_once(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)

    assert await _assimilate(factory, uid, sid, 4.0) == "assimilated"
    assert await _assimilate(factory, uid, sid, 4.0) == "exact_retry"

    rows = await _wellness_rows(factory, uid)
    assert len(rows) == 1
    assert rows[0].correction_requires_replay is False


async def test_concurrent_exact_retries_assimilate_once(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)

    outcomes = await asyncio.gather(*[_assimilate(factory, uid, sid, 4.0) for _ in range(4)])

    assert outcomes.count("assimilated") == 1  # exactly one INSERT won the unique race
    assert all(o in ("assimilated", "exact_retry") for o in outcomes)
    rows = await _wellness_rows(factory, uid)
    assert len(rows) == 1  # one log identity


async def test_changed_content_marks_correction_without_reassimilating(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)

    assert await _assimilate(factory, uid, sid, 4.0) == "assimilated"
    assert await _assimilate(factory, uid, sid, 8.0) == "correction_requires_replay"

    rows = await _wellness_rows(factory, uid)
    assert len(rows) == 1  # NO second assimilation
    row = rows[0]
    assert row.correction_requires_replay is True
    assert row.correction_revision == 1
    assert row.replayed_revision == 0
    assert row.correction_detected_at is not None
    # assimilated hash unchanged; latest hash advanced to the corrected content
    assert row.assimilated_content_hash == wellness_content_hash(WellnessMeasurement(4.0))
    assert row.latest_seen_content_hash == wellness_content_hash(WellnessMeasurement(8.0))


async def test_repeated_correction_self_heals_then_repeat_is_idempotent(factory):
    """A same-day correction self-heals (ADR-0068 head-correction replay), and an identical
    re-POST is an exact retry.

    Production upserts the durable wellness sample BEFORE the shadow runs, so a correction to the
    effective head is replayed on the ingest that detects it — it does not linger pending. A
    second identical correction POST then classifies as an exact retry: no new correction
    generation and no second replay row. (Pre-replay this file asserted the correction stayed
    perpetually pending; that state only arose because the harness never corrected the durable
    source, which the pre-ingest hardening now aborts rather than act on.)
    """
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, soreness=4.0, d=date(2026, 1, 1))  # predecessor belief
    b = await _mk_wellness(factory, uid, soreness=5.0, d=date(2026, 1, 2))  # correction target (head)
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    # Correct B the way production does: durable sample first, then the shadow observation.
    await _set_soreness(factory, b, 8.0)
    assert await _assimilate(factory, uid, b, 8.0) == "correction_requires_replay"

    rows = await _wellness_rows(factory, uid)
    original_b = next(r for r in rows if r.event_type == "update" and r.source_wellness_sample_id == b)
    replays = [r for r in rows if r.event_type == "replay" and r.source_wellness_sample_id == b]
    assert len(replays) == 1                               # self-healed on the detecting ingest
    assert original_b.correction_revision == 1
    assert original_b.replayed_revision == 1               # the generation was replayed
    assert original_b.correction_requires_replay is False  # sticky flag consumed

    # An identical re-POST of the same correction is an exact retry — idempotent.
    assert await _assimilate(factory, uid, b, 8.0) == "exact_retry"
    rows = await _wellness_rows(factory, uid)
    assert len([r for r in rows if r.event_type == "replay" and r.source_wellness_sample_id == b]) == 1
    original_b = next(r for r in rows if r.event_type == "update" and r.source_wellness_sample_id == b)
    assert original_b.correction_revision == 1             # no new generation
    assert original_b.replayed_revision == 1
    assert original_b.correction_requires_replay is False


async def test_content_returning_to_original_keeps_correction_until_replay(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)
    await _assimilate(factory, uid, sid, 4.0)
    await _assimilate(factory, uid, sid, 8.0)  # correction -> sticky flag set

    # Content returns to the originally-assimilated value: an exact retry of the original does
    # NOT clear the pending correction (the trajectory still needs replay).
    assert await _assimilate(factory, uid, sid, 4.0) == "correction_requires_replay"
    rows = await _wellness_rows(factory, uid)
    assert rows[0].correction_requires_replay is True
    assert rows[0].correction_revision == 2  # A -> B -> A is two correction generations


async def test_different_samples_both_assimilate(factory):
    uid = await _seed(factory)
    s1 = await _mk_wellness(factory, uid, soreness=4.0, d=date(2026, 1, 1))
    s2 = await _mk_wellness(factory, uid, soreness=6.0, d=date(2026, 1, 2))

    assert await _assimilate(factory, uid, s1, 4.0) == "assimilated"
    assert await _assimilate(factory, uid, s2, 6.0) == "assimilated"
    assert len(await _wellness_rows(factory, uid)) == 2


async def test_partial_unique_index_is_per_sample_and_model_version(factory):
    """The partial unique index — the concurrency authority — keys on
    (source_wellness_sample_id, model_version) and only for non-NULL source ids: the same
    observation may be assimilated once per independently-versioned model, a second row for the
    same (sample, model) is rejected, and NULL-source (predict/benchmark) rows are unrestricted.
    Tested at the DB level so it does not depend on the belief chain."""
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)
    h = wellness_content_hash(WellnessMeasurement(4.0))

    def _row(model_version: str) -> EkfShadowLog:
        return EkfShadowLog(
            user_id=uid,
            belief_at=datetime.now(UTC).replace(tzinfo=None),
            model_version=model_version,
            event_type="update",
            source_wellness_sample_id=sid,
            assimilated_content_hash=h,
            latest_seen_content_hash=h,
        )

    # Same sample, DIFFERENT model versions -> both allowed.
    async with factory() as db:
        db.add(_row("model_a"))
        db.add(_row("model_b"))
        await db.commit()
    assert len(await _wellness_rows(factory, uid)) == 2

    # Same sample AND model version -> unique violation.
    with pytest.raises(IntegrityError):
        async with factory() as db:
            db.add(_row("model_a"))
            await db.commit()

    # NULL source id (a predict row) is outside the partial index -> unrestricted.
    async with factory() as db:
        db.add(
            EkfShadowLog(
                user_id=uid,
                belief_at=datetime.now(UTC).replace(tzinfo=None),
                model_version="model_a",
                event_type="predict",
            )
        )
        await db.commit()  # no violation


async def test_half_populated_source_identity_rejected_by_check(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)
    with pytest.raises(IntegrityError):
        async with factory() as db:
            db.add(
                EkfShadowLog(
                    user_id=uid,
                    belief_at=datetime.now(UTC).replace(tzinfo=None),
                    model_version="v",
                    event_type="update",
                    source_wellness_sample_id=sid,  # linked, but hashes NULL -> CHECK violation
                )
            )
            await db.commit()


async def test_fresh_session_sees_the_assimilation(factory):
    uid = await _seed(factory)
    sid = await _mk_wellness(factory, uid, soreness=4.0)
    await _assimilate(factory, uid, sid, 4.0)
    # Read through a session that did not create the row — proves the shadow transaction committed.
    assert len(await _wellness_rows(factory, uid)) == 1


async def test_assimilation_failure_leaves_no_claim_and_live_write_survives(
    http_client, async_db, monkeypatch
):
    """An assimilation-path failure rolls the shadow transaction back (no surviving claim, so a
    retry can attempt it again — the claim and belief update share one transaction) and never
    affects the durable wellness upsert. `async_db` is the same session the route used."""
    reg = await http_client.post(
        "/auth/register", json={"email": "c8_fail@test.com", "password": "securepass1"}
    )
    assert reg.status_code == 201, reg.text
    tok = await http_client.post(
        "/auth/token",
        data={"username": "c8_fail@test.com", "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}

    def _boom(*_a, **_k):
        raise RuntimeError("injected assimilation failure")

    monkeypatch.setattr(ekf_shadow_service, "build_wellness_observation", _boom)

    post = await http_client.post(
        "/v1/wellness",
        json={"date": "2026-01-01", "source": "manual", "soreness": 4.0},
        headers=hdr,
    )
    assert post.status_code == 200, post.text  # live write survived the shadow failure

    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    assert len(rows) == 1  # the wellness sample is durable

    # No shadow claim survived the rolled-back assimilation.
    linked = await async_db.scalar(
        select(sa.func.count())
        .select_from(EkfShadowLog)
        .where(EkfShadowLog.source_wellness_sample_id.isnot(None))
    )
    assert (linked or 0) == 0
