"""AUD-C8 head-correction replay — the behavioral contract (a038).

A corrected wellness observation that is still the effective EKF head is repaired by replaying
the trusted update kernel from the ORIGINAL predecessor belief with the corrected content and
appending an ``event_type='replay'`` row that supersedes the head. Mid-history corrections (a
later transition exists) stay pending. The repair fires immediately after the correction is
detected (in a separate best-effort transaction) and is retried before any future assimilation.

Exercised at the service level over per-operation sessions (production shape: ``get_db`` yields
one session per request). The lineage/revision bookkeeping and the compare-and-clear reconcile
are the load-bearing invariants.
"""
from copy import deepcopy
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
    WellnessMeasurement,
    build_wellness_shadow_input,
    wellness_content_hash,
)
from app.models.ekf_shadow import EkfShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services import ekf_shadow_service
from app.services.state_service import initialize_athlete_state
from app.services.telemetry_common import best_effort_write

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(loop_scope="function")
async def factory(_migrated_schema: None):
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRUNCATE_ALL))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _seed(factory, email="replay@test.com") -> int:
    async with factory() as db:
        user = User(email=email, hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await initialize_athlete_state(db, user.id)
        await db.commit()
        return user.id


async def _mk_wellness(factory, user_id: int, soreness: float, d: date) -> int:
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


async def _assimilate(factory, user_id: int, sample_id: int, soreness: float | None) -> str:
    async with factory() as db:
        si = build_wellness_shadow_input(user_id, sample_id, soreness)
        return await ekf_shadow_service.record_ekf_wellness_observation(
            db, user_id, si, observed_at=datetime.now(UTC)
        )


async def _rows(factory, user_id: int) -> list[EkfShadowLog]:
    async with factory() as db:
        res = await db.execute(
            select(EkfShadowLog).where(EkfShadowLog.user_id == user_id).order_by(EkfShadowLog.id)
        )
        return list(res.scalars().all())


def _one(rows, *, event_type, source_id):
    matches = [r for r in rows if r.event_type == event_type and r.source_wellness_sample_id == source_id]
    assert len(matches) == 1, f"expected 1 {event_type} row for sample {source_id}, got {len(matches)}"
    return matches[0]


# ── eligible head correction (also proves the immediate trigger) ─────────────────────────────

async def test_eligible_head_correction_replays_immediately(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 2))
    assert await _assimilate(factory, uid, a, 4.0) == "assimilated"  # predecessor belief
    assert await _assimilate(factory, uid, b, 5.0) == "assimilated"  # target (effective head)
    before = _one(await _rows(factory, uid), event_type="update", source_id=b)
    original_mean = deepcopy(before.mean_json)
    original_covariance = deepcopy(before.covariance_json)

    # Correct B: the replay fires in the SAME ingest that detected the correction (no re-submit).
    await _set_soreness(factory, b, 9.0)
    assert await _assimilate(factory, uid, b, 9.0) == "correction_requires_replay"

    rows = await _rows(factory, uid)
    replays = [r for r in rows if r.event_type == "replay"]
    assert len(replays) == 1
    rp = replays[0]
    original_b = _one(rows, event_type="update", source_id=b)
    base_a = _one(rows, event_type="update", source_id=a)

    assert rp.source_wellness_sample_id == b
    assert rp.supersedes_log_id == original_b.id       # supersedes the row it corrects
    assert rp.replay_base_log_id == base_a.id          # rebuilt from the original predecessor
    assert rp.correction_revision == 1
    assert rp.mean_json != original_b.mean_json        # corrected belief genuinely differs
    assert rp.benchmark_code == "wellness"
    assert rp.innovation is not None and rp.gain_norm is not None
    assert rp.trace_pre is not None and rp.trace_post is not None
    assert rp.nis is not None and rp.n_obs == 2
    # Original numerical history is immutable; only reconciliation metadata advances.
    assert original_b.mean_json == original_mean
    assert original_b.covariance_json == original_covariance
    assert original_b.assimilated_content_hash == wellness_content_hash(WellnessMeasurement(9.0))
    assert original_b.correction_requires_replay is False
    assert original_b.replayed_revision == 1
    assert original_b.replayed_by_log_id == rp.id


async def test_second_correction_rebuilds_from_original_base_not_prior_replay(factory):
    """The original-row-equals-head defect: revision 2 must supersede replay 1 yet rebuild from the
    ORIGINAL predecessor, not assimilate on top of replay 1."""
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)
    await _set_soreness(factory, b, 9.0)
    await _assimilate(factory, uid, b, 9.0)   # replay revision 1

    await _set_soreness(factory, b, 12.0)
    assert await _assimilate(factory, uid, b, 12.0) == "correction_requires_replay"  # revision 2

    rows = await _rows(factory, uid)
    replays = sorted((r for r in rows if r.event_type == "replay"), key=lambda r: r.id)
    assert [r.correction_revision for r in replays] == [1, 2]
    base_a = _one(rows, event_type="update", source_id=a)
    r2 = replays[1]
    assert r2.replay_base_log_id == base_a.id      # ORIGINAL predecessor, not replay 1
    assert r2.supersedes_log_id == replays[0].id   # supersedes the current head (replay 1)
    original_b = _one(rows, event_type="update", source_id=b)
    assert original_b.replayed_revision == 2 and original_b.correction_requires_replay is False


async def test_mid_history_correction_stays_pending(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 2))
    c = await _mk_wellness(factory, uid, 6.0, date(2026, 1, 3))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)
    await _assimilate(factory, uid, c, 6.0)   # head is now C -> B is mid-history

    await _set_soreness(factory, b, 9.0)
    assert await _assimilate(factory, uid, b, 9.0) == "correction_requires_replay"

    rows = await _rows(factory, uid)
    assert not [r for r in rows if r.event_type == "replay"]  # blocked_mid_history: no replay
    original_b = _one(rows, event_type="update", source_id=b)
    assert original_b.correction_requires_replay is True and original_b.replayed_revision == 0


async def test_first_belief_correction_blocks_missing_predecessor(factory):
    uid = await _seed(factory)
    b = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    assert await _assimilate(factory, uid, b, 4.0) == "assimilated"  # first-ever belief, no predecessor

    await _set_soreness(factory, b, 8.0)
    assert await _assimilate(factory, uid, b, 8.0) == "correction_requires_replay"

    rows = await _rows(factory, uid)
    assert not [r for r in rows if r.event_type == "replay"]  # no reseed from current state
    original_b = _one(rows, event_type="update", source_id=b)
    assert original_b.correction_requires_replay is True and original_b.replayed_revision == 0


async def test_failed_replay_is_retried_before_next_assimilation(factory, monkeypatch):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    real_reconcile = ekf_shadow_service._reconcile_after_replay
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected reconcile failure")
        return await real_reconcile(*args, **kwargs)

    monkeypatch.setattr(ekf_shadow_service, "_reconcile_after_replay", flaky)

    # Correction: post-classification replay fires but reconcile fails -> rolled back, stays pending.
    await _set_soreness(factory, b, 9.0)
    assert await _assimilate(factory, uid, b, 9.0) == "correction_requires_replay"
    rows = await _rows(factory, uid)
    assert not [r for r in rows if r.event_type == "replay"]  # the failed replay left nothing
    assert _one(rows, event_type="update", source_id=b).correction_requires_replay is True

    # A later ingest of B: the pre-ingest retry repairs B FIRST (B is still head), then the no-op.
    assert await _assimilate(factory, uid, b, 9.0) == "exact_retry"
    rows = await _rows(factory, uid)
    assert len([r for r in rows if r.event_type == "replay"]) == 1
    original_b = _one(rows, event_type="update", source_id=b)
    assert original_b.correction_requires_replay is False and original_b.replayed_revision == 1


async def test_concurrent_replays_produce_exactly_one_replay_row(factory):
    """Four concurrent replay attempts for the same correction generation -> one replay row.
    The advisory lock serializes them; the first completes and reconciles, the rest see no pending.
    """
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    # Make an eligible-but-unreplayed head correction by classifying WITHOUT the auto-replay.
    await _set_soreness(factory, b, 9.0)
    h9 = wellness_content_hash(WellnessMeasurement(9.0))
    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(EkfShadowLog.source_wellness_sample_id == b, EkfShadowLog.event_type == "update")
            .values(latest_seen_content_hash=h9, correction_revision=1, correction_requires_replay=True)
        )
        await db.commit()

    import asyncio

    async def fire():
        async with factory() as db:
            async with best_effort_write(db, "test concurrent replay"):
                await ekf_shadow_service._acquire_ekf_chain_lock(db, uid)
                return await ekf_shadow_service._replay_pending_head_correction(db, uid, phase="test")

    outcomes = await asyncio.gather(*[fire() for _ in range(4)])
    assert outcomes.count("completed") == 1
    rows = await _rows(factory, uid)
    assert len([r for r in rows if r.event_type == "replay"]) == 1


async def test_reconcile_cannot_clear_a_newer_pending_generation(factory):
    """Compare-and-clear: a revision-1 worker must not clear a revision-2 pending state."""
    uid = await _seed(factory)
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 1, 1))
    await _assimilate(factory, uid, b, 5.0)

    async with factory() as db:
        # Simulate that revision 2 already arrived while a revision-1 replay was in flight.
        await db.execute(
            sa.update(EkfShadowLog)
            .where(EkfShadowLog.source_wellness_sample_id == b, EkfShadowLog.event_type == "update")
            .values(correction_revision=2, replayed_revision=0, correction_requires_replay=True)
        )
        await db.commit()
    original_b = _one(await _rows(factory, uid), event_type="update", source_id=b)

    async with factory() as db:
        reconciled = await ekf_shadow_service._reconcile_after_replay(
            db,
            original_b.id,
            claimed_revision=1,
            claimed_hash=original_b.latest_seen_content_hash or "",
            replay_id=original_b.id,
        )
        await db.commit()

    assert reconciled is False
    refreshed = _one(await _rows(factory, uid), event_type="update", source_id=b)
    assert refreshed.replayed_revision == 0
    assert refreshed.replayed_by_log_id is None
    assert refreshed.correction_requires_replay is True  # rev 2 still pending -> untouched


# ── Q9 uniqueness regression (DB-level) ───────────────────────────────────────────────────────

async def test_original_and_replay_coexist_but_each_is_singular(factory):
    uid = await _seed(factory)
    b = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    h = wellness_content_hash(WellnessMeasurement(4.0))

    def _update_row(model="ekf-v1"):
        return EkfShadowLog(
            user_id=uid, belief_at=datetime.now(UTC).replace(tzinfo=None), model_version=model,
            event_type="update", source_wellness_sample_id=b,
            assimilated_content_hash=h, latest_seen_content_hash=h,
        )

    def _replay_row(revision, base_id, super_id, model="ekf-v1"):
        return EkfShadowLog(
            user_id=uid, belief_at=datetime.now(UTC).replace(tzinfo=None), model_version=model,
            event_type="replay", source_wellness_sample_id=b,
            assimilated_content_hash=h, latest_seen_content_hash=h,
            correction_revision=revision, supersedes_log_id=super_id, replay_base_log_id=base_id,
        )

    # Original update + a replay for the same (source, model) coexist.
    async with factory() as db:
        db.add(_update_row())
        await db.commit()
        orig_id = (await db.scalar(select(EkfShadowLog.id).where(
            EkfShadowLog.source_wellness_sample_id == b, EkfShadowLog.event_type == "update"))) or 0
    async with factory() as db:
        db.add(_replay_row(revision=1, base_id=orig_id, super_id=orig_id))
        await db.commit()  # allowed alongside the original
    assert len(await _rows(factory, uid)) == 2

    # A SECOND original assimilation for the same (source, model) is rejected.
    with pytest.raises(IntegrityError):
        async with factory() as db:
            db.add(_update_row())
            await db.commit()

    # The SAME replay generation twice is rejected...
    with pytest.raises(IntegrityError):
        async with factory() as db:
            db.add(_replay_row(revision=1, base_id=orig_id, super_id=orig_id))
            await db.commit()

    # ...but the NEXT generation is allowed, as is the SAME generation under a different model.
    async with factory() as db:
        db.add(_replay_row(revision=2, base_id=orig_id, super_id=orig_id))
        db.add(_replay_row(revision=1, base_id=orig_id, super_id=orig_id, model="ekf-v2"))
        await db.commit()
    assert len([r for r in await _rows(factory, uid) if r.event_type == "replay"]) == 3


async def test_replay_row_requires_complete_lineage(factory):
    uid = await _seed(factory)
    b = await _mk_wellness(factory, uid, 4.0, date(2026, 1, 1))
    h = wellness_content_hash(WellnessMeasurement(4.0))
    with pytest.raises(IntegrityError):
        async with factory() as db:
            db.add(EkfShadowLog(  # replay row missing supersedes/base/revision -> CHECK violation
                user_id=uid, belief_at=datetime.now(UTC).replace(tzinfo=None), model_version="ekf-v1",
                event_type="replay", source_wellness_sample_id=b,
                assimilated_content_hash=h, latest_seen_content_hash=h,
            ))
            await db.commit()


async def test_classifier_updates_only_original_and_leaves_prior_replay_immutable(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 2, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 2, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    await _set_soreness(factory, b, 8.0)
    await _assimilate(factory, uid, b, 8.0)
    rows = await _rows(factory, uid)
    replay1 = next(r for r in rows if r.event_type == "replay" and r.correction_revision == 1)
    frozen = {
        "assimilated_content_hash": replay1.assimilated_content_hash,
        "latest_seen_content_hash": replay1.latest_seen_content_hash,
        "correction_revision": replay1.correction_revision,
        "supersedes_log_id": replay1.supersedes_log_id,
        "replay_base_log_id": replay1.replay_base_log_id,
        "mean_json": deepcopy(replay1.mean_json),
        "covariance_json": deepcopy(replay1.covariance_json),
    }

    await _set_soreness(factory, b, 9.0)
    await _assimilate(factory, uid, b, 9.0)

    rows = await _rows(factory, uid)
    replay1_after = next(r for r in rows if r.id == replay1.id)
    assert {
        "assimilated_content_hash": replay1_after.assimilated_content_hash,
        "latest_seen_content_hash": replay1_after.latest_seen_content_hash,
        "correction_revision": replay1_after.correction_revision,
        "supersedes_log_id": replay1_after.supersedes_log_id,
        "replay_base_log_id": replay1_after.replay_base_log_id,
        "mean_json": replay1_after.mean_json,
        "covariance_json": replay1_after.covariance_json,
    } == frozen
    original = _one(rows, event_type="update", source_id=b)
    assert original.correction_revision == 2
    assert original.replayed_revision == 2


async def test_correction_that_removes_soreness_retracts_to_exact_predecessor(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 3, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 3, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    await _set_soreness(factory, b, None)
    assert await _assimilate(factory, uid, b, None) == "correction_requires_replay"

    rows = await _rows(factory, uid)
    base = _one(rows, event_type="update", source_id=a)
    original = _one(rows, event_type="update", source_id=b)
    replay = _one(rows, event_type="replay", source_id=b)
    assert replay.replay_base_log_id == base.id
    assert replay.mean_json == base.mean_json
    assert replay.covariance_json == base.covariance_json
    assert replay.benchmark_code is None
    assert replay.innovation is None and replay.nis is None
    null_hash = wellness_content_hash(WellnessMeasurement(None))
    assert replay.assimilated_content_hash == null_hash
    assert original.assimilated_content_hash == null_hash
    assert original.correction_requires_replay is False


async def test_unsupported_model_version_stays_pending(factory, caplog):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 4, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 4, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(EkfShadowLog.user_id == uid)
            .values(model_version="ekf-v0")
        )
        await db.commit()

    await _set_soreness(factory, b, 8.0)
    with caplog.at_level("INFO", logger=ekf_shadow_service.__name__):
        assert await _assimilate(factory, uid, b, 8.0) == "correction_requires_replay"

    rows = await _rows(factory, uid)
    assert not [r for r in rows if r.event_type == "replay"]
    original = _one(rows, event_type="update", source_id=b)
    assert original.correction_requires_replay is True
    assert original.replayed_revision == 0
    assert "outcome=blocked_unsupported_version" in caplog.text


async def test_preexisting_replay_and_current_failure_roll_back_together(
    factory, monkeypatch, caplog
):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 5, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 5, 2))
    c = await _mk_wellness(factory, uid, 6.0, date(2026, 5, 3))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    await _set_soreness(factory, b, 9.0)
    pending_hash = wellness_content_hash(WellnessMeasurement(9.0))
    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(
                EkfShadowLog.source_wellness_sample_id == b,
                EkfShadowLog.event_type == "update",
            )
            .values(
                latest_seen_content_hash=pending_hash,
                correction_revision=1,
                correction_requires_replay=True,
            )
        )
        await db.commit()

    async def fail_current(*args, **kwargs):
        raise RuntimeError("injected current-assimilation failure")

    monkeypatch.setattr(ekf_shadow_service, "_assimilate_or_classify", fail_current)
    with caplog.at_level("INFO", logger=ekf_shadow_service.__name__):
        assert await _assimilate(factory, uid, c, 6.0) == "skipped"
    assert "phase=pre_ingest outcome=failed" in caplog.text
    assert "phase=pre_ingest outcome=completed" not in caplog.text

    rows = await _rows(factory, uid)
    assert not [r for r in rows if r.event_type == "replay"]
    assert not [
        r
        for r in rows
        if r.event_type == "update" and r.source_wellness_sample_id == c
    ]
    original = _one(rows, event_type="update", source_id=b)
    assert original.correction_requires_replay is True
    assert original.replayed_revision == 0
    assert original.assimilated_content_hash == wellness_content_hash(WellnessMeasurement(5.0))


async def test_unexplained_pending_source_change_aborts_before_unrelated_append(factory):
    """A source mutation not represented by the current input must not turn a head fix mid-history."""
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 5, 10))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 5, 11))
    c = await _mk_wellness(factory, uid, 6.0, date(2026, 5, 12))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    h8 = wellness_content_hash(WellnessMeasurement(8.0))
    await _set_soreness(factory, b, 9.0)  # durable source moved beyond the classified rev 1
    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(
                EkfShadowLog.source_wellness_sample_id == b,
                EkfShadowLog.event_type == "update",
            )
            .values(
                latest_seen_content_hash=h8,
                correction_revision=1,
                correction_requires_replay=True,
            )
        )
        await db.commit()

    # C is unrelated to B's unclassified source movement. The entire shadow transaction aborts.
    assert await _assimilate(factory, uid, c, 6.0) == "skipped"
    rows = await _rows(factory, uid)
    assert not [
        row
        for row in rows
        if row.event_type == "update" and row.source_wellness_sample_id == c
    ]
    assert not [row for row in rows if row.event_type == "replay"]
    original = _one(rows, event_type="update", source_id=b)
    assert original.latest_seen_content_hash == h8
    assert original.correction_revision == 1
    assert original.correction_requires_replay is True


async def test_current_input_can_advance_stale_pending_source_then_replay_latest(factory):
    """The same source input may explain the source mismatch, advance the revision, and replay it."""
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 5, 20))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 5, 21))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    h8 = wellness_content_hash(WellnessMeasurement(8.0))
    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(
                EkfShadowLog.source_wellness_sample_id == b,
                EkfShadowLog.event_type == "update",
            )
            .values(
                latest_seen_content_hash=h8,
                correction_revision=1,
                correction_requires_replay=True,
            )
        )
        await db.commit()
    await _set_soreness(factory, b, 9.0)

    assert await _assimilate(factory, uid, b, 9.0) == "correction_requires_replay"
    rows = await _rows(factory, uid)
    replays = [row for row in rows if row.event_type == "replay"]
    assert len(replays) == 1
    assert replays[0].correction_revision == 2
    assert replays[0].replay_base_log_id == _one(
        rows, event_type="update", source_id=a
    ).id
    original = _one(rows, event_type="update", source_id=b)
    assert original.correction_revision == 2
    assert original.replayed_revision == 2
    assert original.correction_requires_replay is False


async def test_fresh_session_observes_durable_replay_head_and_history(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 6, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 6, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)
    await _set_soreness(factory, b, 7.0)
    await _assimilate(factory, uid, b, 7.0)

    async with factory() as fresh:
        rows = list(
            (
                await fresh.execute(
                    select(EkfShadowLog)
                    .where(EkfShadowLog.user_id == uid)
                    .order_by(EkfShadowLog.id)
                )
            )
            .scalars()
            .all()
        )
        head = await ekf_shadow_service._load_latest_belief(fresh, uid)

    assert [r.event_type for r in rows] == ["update", "update", "replay"]
    assert head is not None and head.id == rows[-1].id
    original = _one(rows, event_type="update", source_id=b)
    assert original.mean_json != head.mean_json
    assert original.replayed_by_log_id == head.id
    assert original.correction_requires_replay is False


async def test_replay_and_unrelated_benchmark_appender_serialize_without_fork(factory):
    uid = await _seed(factory)
    a = await _mk_wellness(factory, uid, 4.0, date(2026, 8, 1))
    b = await _mk_wellness(factory, uid, 5.0, date(2026, 8, 2))
    await _assimilate(factory, uid, a, 4.0)
    await _assimilate(factory, uid, b, 5.0)

    await _set_soreness(factory, b, 9.0)
    pending_hash = wellness_content_hash(WellnessMeasurement(9.0))
    async with factory() as db:
        await db.execute(
            sa.update(EkfShadowLog)
            .where(
                EkfShadowLog.source_wellness_sample_id == b,
                EkfShadowLog.event_type == "update",
            )
            .values(
                latest_seen_content_hash=pending_hash,
                correction_revision=1,
                correction_requires_replay=True,
            )
        )
        await db.commit()

    import asyncio

    async def fire_replay():
        async with factory() as db:
            async with best_effort_write(db, "test replay/appender race"):
                await ekf_shadow_service._acquire_ekf_chain_lock(db, uid)
                return await ekf_shadow_service._replay_pending_head_correction(
                    db, uid, phase="test"
                )

    async def fire_benchmark():
        async with factory() as db:
            specs = [
                ekf_shadow_service.MappingSpec(
                    target_vector="capacity",
                    target_key="max_strength",
                    coefficient=1.0,
                )
            ]
            await ekf_shadow_service.record_ekf_update(
                db,
                uid,
                benchmark_code="1rm",
                mapping_specs=specs,
                score01=0.8,
                observed_at=datetime.now(UTC),
            )

    replay_outcome, _ = await asyncio.gather(fire_replay(), fire_benchmark())
    rows = await _rows(factory, uid)
    benchmark = next(
        r
        for r in rows
        if r.event_type == "update"
        and r.source_wellness_sample_id is None
        and r.benchmark_code == "1rm"
    )
    replays = [r for r in rows if r.event_type == "replay"]
    original = _one(rows, event_type="update", source_id=b)

    if replays:
        assert replay_outcome == "completed"
        assert len(replays) == 1
        assert replays[0].id < benchmark.id
        assert replays[0].supersedes_log_id == original.id
        assert original.correction_requires_replay is False
    else:
        assert replay_outcome == "blocked_mid_history"
        assert benchmark.id > original.id
        assert original.correction_requires_replay is True
