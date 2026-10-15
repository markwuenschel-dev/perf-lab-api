"""The shadow evidence reader against P7b-shaped data (P7a ships it, so a rollback from P7b
never shows a skip, a retired row or a stale row as current output).

Rows are inserted directly in the shapes P7b writes: correction chains per sample and model
version, computed rows and ``skipped_not_as_of_day`` rows, plus unlinked legacy rows.
"""
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services import shadow_summary_service
from app.services.shadow_evidence import (
    RECORDED,
    SKIPPED_NOT_AS_OF_DAY,
    inputs_hash,
    latest_current_evidence,
)

pytestmark = pytest.mark.asyncio

_MULT = {"f_met_systemic": 1.0}


async def _user(db: AsyncSession, email: str) -> int:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    return user.id


async def _sample(db: AsyncSession, uid: int, day: date, hrv: float) -> tuple[int, str]:
    sample = WellnessSample(user_id=uid, date=day, source="oura", hrv_ms=hrv)
    db.add(sample)
    await db.commit()
    snap = WellnessTelemetrySnapshot(sleep_hours=None, hrv_ms=hrv, resting_hr=None,
                                     soreness=None, mood=None)
    return sample.id, inputs_hash(snap)


def _hash(hrv: float) -> str:
    return inputs_hash(WellnessTelemetrySnapshot(sleep_hours=None, hrv_ms=hrv, resting_hr=None,
                                                 soreness=None, mood=None))


async def _recovery(db: AsyncSession, uid: int, *, sample: int | None = None,
                    digest: str | None = None, supersedes: int | None = None, mv: str = "v1",
                    impact: str = RECORDED) -> int:
    computed = impact == RECORDED
    row = RecoveryShadowLog(
        user_id=uid, model_version=mv, wellness={}, fatigue_before={},
        baseline_clearance_multiplier=_MULT if computed else {},
        learned_clearance_multiplier=_MULT if computed else {},
        decision_impact=impact, source_wellness_sample_id=sample, content_hash=digest,
        supersedes_id=supersedes,
    )
    db.add(row)
    await db.commit()
    return row.id


async def _personalization(db: AsyncSession, uid: int, *, sample: int | None = None,
                           digest: str | None = None, supersedes: int | None = None,
                           mv: str = "v1", parameter: str = "recovery_beta") -> int:
    row = PersonalizationShadowLog(
        user_id=uid, parameter=parameter, model_version=mv, n_obs=0, shrinkage_weight=0.0,
        theta_trace=0.0, wellness={}, population_multiplier=_MULT, personalized_multiplier=_MULT,
        decision_impact=RECORDED, source_wellness_sample_id=sample, content_hash=digest,
        supersedes_id=supersedes,
    )
    db.add(row)
    await db.commit()
    return row.id


async def _current(db: AsyncSession, model, uid: int) -> int | None:
    db.expire_all()
    row = await latest_current_evidence(db, model, uid)
    return None if row is None else row.id


async def test_legacy_rows_count_until_linked_evidence_follows_them(async_db):
    uid = await _user(async_db, "ev-legacy@test.com")
    legacy = await _recovery(async_db, uid)
    assert await _current(async_db, RecoveryShadowLog, uid) == legacy

    sid, digest = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    skip = await _recovery(async_db, uid, sample=sid, digest=digest, impact=SKIPPED_NOT_AS_OF_DAY)
    assert skip  # a linked skip alone retires the legacy computation
    assert await _current(async_db, RecoveryShadowLog, uid) is None


async def test_a_skip_retires_its_samples_computation_across_model_versions(async_db):
    uid = await _user(async_db, "ev-versions@test.com")
    sid, _ = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    computed = await _recovery(async_db, uid, sample=sid, digest=_hash(60.0), mv="v1")
    assert await _current(async_db, RecoveryShadowLog, uid) == computed

    # Corrected to 72 a day later with the artifact gone: a skip in the "none" chain.
    sample = await async_db.get(WellnessSample, sid)
    assert sample is not None
    sample.hrv_ms = 72.0
    await async_db.commit()
    await _recovery(async_db, uid, sample=sid, digest=_hash(72.0), mv="none",
                    impact=SKIPPED_NOT_AS_OF_DAY)
    assert await _current(async_db, RecoveryShadowLog, uid) is None
    assert (await shadow_summary_service.athlete_shadow_summary(async_db, uid))["recovery"] is None


async def test_a_superseded_computation_gives_way_to_its_successor(async_db):
    uid = await _user(async_db, "ev-chain@test.com")
    sid, _ = await _sample(async_db, uid, date(2026, 10, 1), 72.0)
    root = await _recovery(async_db, uid, sample=sid, digest=_hash(60.0))
    head = await _recovery(async_db, uid, sample=sid, digest=_hash(72.0), supersedes=root)
    assert await _current(async_db, RecoveryShadowLog, uid) == head


async def test_a_row_whose_inputs_are_no_longer_committed_does_not_count(async_db):
    # A newer write's own shadow failed: the head was computed from values the sample no
    # longer holds. An older sample's fresh computation is the current evidence instead.
    uid = await _user(async_db, "ev-stale@test.com")
    older, older_digest = await _sample(async_db, uid, date(2026, 10, 1), 55.0)
    fresh = await _recovery(async_db, uid, sample=older, digest=older_digest)
    sid, _ = await _sample(async_db, uid, date(2026, 10, 2), 72.0)
    await _recovery(async_db, uid, sample=sid, digest=_hash(60.0))  # computed from 60
    assert await _current(async_db, RecoveryShadowLog, uid) == fresh


async def test_a_later_samples_skip_leaves_an_earlier_samples_computation(async_db):
    uid = await _user(async_db, "ev-days@test.com")
    s1, d1 = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    s2, d2 = await _sample(async_db, uid, date(2026, 9, 30), 61.0)
    computed = await _recovery(async_db, uid, sample=s1, digest=d1)
    await _recovery(async_db, uid, sample=s2, digest=d2, impact=SKIPPED_NOT_AS_OF_DAY)
    assert await _current(async_db, RecoveryShadowLog, uid) == computed


async def test_personalization_is_scoped_by_parameter_and_by_athlete(async_db):
    uid = await _user(async_db, "ev-param@test.com")
    other = await _user(async_db, "ev-param-other@test.com")
    sid, digest = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    beta = await _personalization(async_db, uid, sample=sid, digest=digest)
    # A later row of ANOTHER parameter, itself not current (stale inputs), must not retire it.
    await _personalization(async_db, uid, sample=sid, digest=_hash(99.0), parameter="other_beta")
    assert await _current(async_db, PersonalizationShadowLog, uid) == beta
    assert await _current(async_db, PersonalizationShadowLog, other) is None
    summary = await shadow_summary_service.athlete_shadow_summary(async_db, uid)
    assert summary["personalization"] is not None


async def test_a_later_skip_retires_a_still_fresh_computation_in_another_version(async_db):
    # Same values throughout, so freshness alone cannot reject the computed row: only the
    # later row for the same sample (a skip written under another model version) retires it.
    uid = await _user(async_db, "ev-fresh-retired@test.com")
    sid, digest = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    await _recovery(async_db, uid, sample=sid, digest=digest, mv="v1")
    await _recovery(async_db, uid, sample=sid, digest=digest, mv="none",
                    impact=SKIPPED_NOT_AS_OF_DAY)
    assert await _current(async_db, RecoveryShadowLog, uid) is None


async def test_an_unlinked_row_newer_than_every_linked_row_counts(async_db):
    # A rollback from P7b: P7a's writer adds unlinked rows after the athlete's linked history.
    # They are newer than any linked row, so they count; the legacy row from before P7b, and
    # the stale linked row, do not.
    uid = await _user(async_db, "ev-rollback@test.com")
    await _recovery(async_db, uid)  # legacy, before P7b
    sid, _ = await _sample(async_db, uid, date(2026, 10, 1), 72.0)
    await _recovery(async_db, uid, sample=sid, digest=_hash(60.0))  # stale P7b computation
    assert await _current(async_db, RecoveryShadowLog, uid) is None
    after_rollback = await _recovery(async_db, uid)
    assert await _current(async_db, RecoveryShadowLog, uid) == after_rollback

    # Rolled forward again: P7b's next linked row retires it like any other unlinked row.
    s2, d2 = await _sample(async_db, uid, date(2026, 10, 2), 61.0)
    await _recovery(async_db, uid, sample=s2, digest=d2, impact=SKIPPED_NOT_AS_OF_DAY)
    assert await _current(async_db, RecoveryShadowLog, uid) is None


async def test_newer_stale_heads_never_hide_an_older_valid_computation(async_db):
    # More stale heads than any page the reader might use (the stream fetches 100 at a time).
    uid = await _user(async_db, "ev-scan@test.com")
    older, older_digest = await _sample(async_db, uid, date(2026, 1, 1), 55.0)
    valid = await _recovery(async_db, uid, sample=older, digest=older_digest)
    for offset in range(120):
        sid, _ = await _sample(async_db, uid, date(2026, 1, 2) + timedelta(days=offset), 72.0)
        await _recovery(async_db, uid, sample=sid, digest=_hash(60.0))  # its newer hook failed
    assert await _current(async_db, RecoveryShadowLog, uid) == valid


async def test_a_row_and_its_samples_values_are_judged_in_one_snapshot(async_db, monkeypatch):
    """The review's reproduction: computed A is stale (the sample holds B); the reader starts;
    B's skip then retires A and the sample returns to A. A was never current during the read,
    so the reader must not return it, whatever it reads after the changes commit."""
    uid = await _user(async_db, "ev-snapshot@test.com")
    sid, _ = await _sample(async_db, uid, date(2026, 10, 1), 60.0)
    await _recovery(async_db, uid, sample=sid, digest=_hash(60.0))  # computed A
    sample = await async_db.get(WellnessSample, sid)
    assert sample is not None
    sample.hrv_ms = 72.0  # B commits; its own hook has not run yet
    await async_db.commit()

    paused, release = asyncio.Event(), asyncio.Event()
    factory = async_sessionmaker(async_db.bind, expire_on_commit=False)
    async with factory() as reader:
        real_stream = reader.stream

        async def _stream_then_pause(*args, **kwargs):
            result = await real_stream(*args, **kwargs)  # the statement is open: snapshot taken
            paused.set()
            await release.wait()
            return result

        monkeypatch.setattr(reader, "stream", _stream_then_pause)
        task = asyncio.create_task(latest_current_evidence(reader, RecoveryShadowLog, uid))
        await asyncio.wait_for(paused.wait(), 15)
        await _recovery(async_db, uid, sample=sid, digest=_hash(72.0), mv="none",
                        impact=SKIPPED_NOT_AS_OF_DAY)  # B's delayed hook retires A
        sample = await async_db.get(WellnessSample, sid)
        assert sample is not None
        sample.hrv_ms = 60.0  # back to A; its hook never runs
        await async_db.commit()
        release.set()
        assert await asyncio.wait_for(task, 15) is None
    assert await _current(async_db, RecoveryShadowLog, uid) is None  # a fresh read agrees
