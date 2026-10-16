"""P7: every wellness source runs the shadows, as correction chains (a062), through the real
routes (requires a live DB).

Per source: recovery records only for a sample of the as-of UTC day and writes an explicit skip
row otherwise; personalization records every day; the EKF assimilates manual soreness and skips
wearable sources. An unchanged re-sync adds nothing, a correction appends exactly one
superseding row, a delayed snapshot never supersedes newer committed inputs, and a key conflict
is re-read rather than lost.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.integrations.base import NormalizedWellness, TokenBundle
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.ekf_shadow import EkfShadowLog
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.schemas.wellness import WellnessSampleIn
from app.services import (
    personalization_shadow_service,
    readiness_service,
    recovery_shadow_service,
    shadow_summary_service,
    wearable_service,
    wellness_shadow_chain,
    wellness_shadow_hooks,
)
from app.services.shadow_evidence import RECORDED, SKIPPED_NOT_AS_OF_DAY, inputs_hash
from app.services.wellness_shadow_hooks import sample_ref

pytestmark = pytest.mark.asyncio

AS_OF = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
TODAY = AS_OF.date()
WEEK = [TODAY - timedelta(days=offset) for offset in range(6, -1, -1)]  # oldest first


class _FakeOura:
    provider = "oura"

    def __init__(self, readings: list[NormalizedWellness]):
        self.readings = readings

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        return TokenBundle(access_token="acc2", refresh_token="ref2")

    async def fetch_daily_wellness(self, access_token, start, end):
        return self.readings


def _reading(day: date, hrv: float = 60.0) -> NormalizedWellness:
    return NormalizedWellness(day=day, hrv_ms=hrv, sleep_hours=7.4, sleep_quality=85.0,
                              resting_hr=50.0)


@pytest.fixture
def oura(monkeypatch) -> _FakeOura:
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    fake = _FakeOura([_reading(d) for d in WEEK])
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": fake)
    yield fake
    crypto._fernet_for.cache_clear()


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    monkeypatch.setattr(wellness_shadow_hooks, "_now", lambda: AS_OF)


async def _athlete(client, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


async def _connected(client, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    login, uid = await _athlete(client, db, email)
    r = await client.post("/v1/integrations/oura/connect/pat", json={"token": "pat-abc-12345"},
                          headers=login)
    assert r.status_code == 200, r.text
    return login, uid


async def _sync(client, login: dict[str, str]) -> None:
    r = await client.post("/v1/integrations/oura/sync", headers=login)
    assert r.status_code == 200, r.text


async def _rows(db: AsyncSession, model, uid: int) -> list:
    db.expire_all()
    return list((await db.execute(
        select(model).where(model.user_id == uid).order_by(model.id)
    )).scalars().all())


async def _samples(db: AsyncSession, uid: int) -> dict[date, int]:
    """Sample ids by day, as plain data (a later expire_all must not reach them)."""
    db.expire_all()
    rows = await db.execute(select(WellnessSample.date, WellnessSample.id)
                            .where(WellnessSample.user_id == uid))
    return dict(rows.tuples().all())


def _heads(rows: list) -> list:
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if r.id not in superseded]


# ── a week of Oura ───────────────────────────────────────────────────────────────────


async def test_a_week_of_oura_runs_each_shadow_per_day_and_a_resync_adds_nothing(
    http_client, async_db, oura, monkeypatch
):
    login, uid = await _connected(http_client, async_db, "p7-week@test.com")
    await _sync(http_client, login)

    samples = await _samples(async_db, uid)
    assert sorted(samples) == WEEK
    recovery = await _rows(async_db, RecoveryShadowLog, uid)
    by_day = {r.source_wellness_sample_id: r for r in recovery}
    assert len(recovery) == 7 and len(by_day) == 7
    for day, sample_id in samples.items():
        row = by_day[sample_id]
        assert row.supersedes_id is None and row.content_hash
        if day == TODAY:  # the only day its current fatigue state belongs to
            assert row.decision_impact == RECORDED and row.baseline_clearance_multiplier
        else:
            assert row.decision_impact == SKIPPED_NOT_AS_OF_DAY
            assert row.fatigue_before == {} and row.baseline_clearance_multiplier == {}
            assert row.wellness["hrv_ms"] == 60.0  # the skip still says what it declined
    personalization = await _rows(async_db, PersonalizationShadowLog, uid)
    assert {r.source_wellness_sample_id for r in personalization} == set(samples.values())
    assert all(r.decision_impact == RECORDED for r in personalization)
    assert await _rows(async_db, EkfShadowLog, uid) == []  # Oura supplies no soreness

    # Identical days: nothing is written, and nothing is recomputed either (no state read for
    # recovery, no history frame for personalization), so a re-sync costs only the head checks.
    calls = {"state": 0, "frame": 0}
    real_state = recovery_shadow_service.load_current_state
    real_frame = personalization_shadow_service._build_recovery_frame

    async def _counted_state(*args, **kwargs):
        calls["state"] += 1
        return await real_state(*args, **kwargs)

    async def _counted_frame(*args, **kwargs):
        calls["frame"] += 1
        return await real_frame(*args, **kwargs)

    monkeypatch.setattr(recovery_shadow_service, "load_current_state", _counted_state)
    monkeypatch.setattr(personalization_shadow_service, "_build_recovery_frame", _counted_frame)
    await _sync(http_client, login)
    assert calls == {"state": 0, "frame": 0}
    assert len(await _rows(async_db, RecoveryShadowLog, uid)) == 7
    assert len(await _rows(async_db, PersonalizationShadowLog, uid)) == 7


async def test_a_corrected_day_appends_exactly_one_superseding_row_per_chain(
    http_client, async_db, oura
):
    login, uid = await _connected(http_client, async_db, "p7-correct@test.com")
    await _sync(http_client, login)
    earlier = WEEK[2]
    oura.readings = [_reading(d, hrv=72.0 if d in (earlier, TODAY) else 60.0) for d in WEEK]
    await _sync(http_client, login)

    samples = await _samples(async_db, uid)
    for model in (RecoveryShadowLog, PersonalizationShadowLog):
        rows = await _rows(async_db, model, uid)
        assert len(rows) == 9
        for day in (earlier, TODAY):
            chain = [r for r in rows if r.source_wellness_sample_id == samples[day]]
            assert len(chain) == 2 and chain[1].supersedes_id == chain[0].id
            assert _heads(chain) == [chain[1]] and chain[1].wellness["hrv_ms"] == 72.0
    recovery = await _rows(async_db, RecoveryShadowLog, uid)
    today_head = _heads([r for r in recovery if r.source_wellness_sample_id == samples[TODAY]])
    assert today_head[0].decision_impact == RECORDED
    earlier_head = _heads([r for r in recovery if r.source_wellness_sample_id == samples[earlier]])
    assert earlier_head[0].decision_impact == SKIPPED_NOT_AS_OF_DAY


@pytest.mark.parametrize("failing", ["recovery", "personalization"])
async def test_each_shadow_failing_alone_leaves_the_sync_and_the_other_shadow(
    http_client, async_db, oura, monkeypatch, failing
):
    module = recovery_shadow_service if failing == "recovery" else personalization_shadow_service

    async def _boom(*args, **kwargs):
        raise RuntimeError(f"injected {failing} failure")

    monkeypatch.setattr(module, "_append", _boom)
    login, uid = await _connected(http_client, async_db, f"p7-fail-{failing}@test.com")
    await _sync(http_client, login)

    assert len(await _samples(async_db, uid)) == 7
    recovery = await _rows(async_db, RecoveryShadowLog, uid)
    personalization = await _rows(async_db, PersonalizationShadowLog, uid)
    assert (len(recovery), len(personalization)) == ((0, 7) if failing == "recovery" else (7, 0))


async def test_personalization_skips_with_no_population_artifact(http_client, async_db, oura,
                                                                 monkeypatch):
    monkeypatch.setattr(personalization_shadow_service, "load_namespace_override", lambda ns: None)
    login, uid = await _connected(http_client, async_db, "p7-noartifact@test.com")
    await _sync(http_client, login)
    assert await _rows(async_db, PersonalizationShadowLog, uid) == []
    assert len(await _rows(async_db, RecoveryShadowLog, uid)) == 7


# ── manual and phone ─────────────────────────────────────────────────────────────────


async def _checkin(client, login, day: date, **values) -> None:
    body = {"date": day.isoformat(), "source": "manual", "sleep_hours": 7.0, "hrv_ms": 60.0,
            "resting_hr": 52.0, **values}
    r = await client.post("/v1/wellness", json=body, headers=login)
    assert r.status_code == 200, r.text


async def test_a_backdated_checkin_keeps_the_sample_and_records_a_skip(http_client, async_db):
    login, uid = await _athlete(http_client, async_db, "p7-backdated@test.com")
    await _checkin(http_client, login, TODAY - timedelta(days=1), soreness=3.0)

    assert len(await _samples(async_db, uid)) == 1
    recovery = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.decision_impact for r in recovery] == [SKIPPED_NOT_AS_OF_DAY]
    assert [r.decision_impact for r in await _rows(async_db, PersonalizationShadowLog, uid)] == [
        RECORDED]


async def test_an_ineligible_correction_retires_the_old_row_from_current_evidence(
    http_client, async_db, monkeypatch
):
    login, uid = await _athlete(http_client, async_db, "p7-retire@test.com")
    await _checkin(http_client, login, TODAY)
    summary = await shadow_summary_service.athlete_shadow_summary(async_db, uid)
    assert summary["recovery"] is not None  # today's computed row is current evidence

    monkeypatch.setattr(wellness_shadow_hooks, "_now", lambda: AS_OF + timedelta(days=1))
    await _checkin(http_client, login, TODAY, hrv_ms=75.0)  # corrected a day later

    recovery = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.decision_impact for r in recovery] == [RECORDED, SKIPPED_NOT_AS_OF_DAY]
    assert recovery[1].supersedes_id == recovery[0].id
    async_db.expire_all()
    summary = await shadow_summary_service.athlete_shadow_summary(async_db, uid)
    assert summary["recovery"] is None  # the superseded row no longer counts


async def test_a_correction_back_to_an_earlier_value_appends_a_row(http_client, async_db):
    login, uid = await _athlete(http_client, async_db, "p7-aba@test.com")
    for hrv in (60.0, 70.0, 60.0):
        await _checkin(http_client, login, TODAY, hrv_ms=hrv)
    for model in (RecoveryShadowLog, PersonalizationShadowLog):
        rows = await _rows(async_db, model, uid)
        assert [r.wellness["hrv_ms"] for r in rows] == [60.0, 70.0, 60.0]
        assert _heads(rows) == [rows[2]] and rows[2].content_hash == rows[0].content_hash


async def test_a_phone_push_runs_the_shadows_and_skips_the_ekf(http_client, async_db):
    login, uid = await _athlete(http_client, async_db, "p7-phone@test.com")
    resp = await http_client.post("/v1/wellness/ingest-tokens", json={"label": "iPhone"},
                                  headers=login)
    push = {"Authorization": f"Bearer {resp.json()['token']}"}
    r = await http_client.post("/v1/wellness/ingest", headers=push, json={
        "source": "apple_watch", "date": TODAY.isoformat(), "hrv_ms": 48.0, "resting_hr": 52.0,
        "sleep_hours": 7.1})
    assert r.status_code in (200, 201), r.text
    assert [r.decision_impact for r in await _rows(async_db, RecoveryShadowLog, uid)] == [RECORDED]
    assert len(await _rows(async_db, PersonalizationShadowLog, uid)) == 1
    assert await _rows(async_db, EkfShadowLog, uid) == []


async def test_a_wearable_sample_is_an_explicit_ekf_skip_even_with_soreness(async_db):
    # No provider sends soreness today; if one ever did, a wearable source still must not reach
    # the EKF until that is decided (HRV/sleep/soreness assimilation from wearables is out of
    # scope for P7), and the skip is reported, not silent.
    uid = await _plain_user(async_db, "p7-ekf-skip@test.com")
    sample = await readiness_service.upsert_wellness_sample(async_db, uid, WellnessSampleIn(
        date=TODAY, source="oura", hrv_ms=60.0, soreness=4.0))
    outcomes = await wellness_shadow_hooks.run_wellness_shadows(
        async_db, sample, assimilate_soreness=False)
    assert outcomes.ekf == "skipped_no_soreness_source"
    assert await _rows(async_db, EkfShadowLog, uid) == []


# ── ordering and conflicts ───────────────────────────────────────────────────────────


async def _sample(db: AsyncSession, uid: int, hrv: float, day: date = TODAY) -> WellnessSample:
    return await readiness_service.upsert_wellness_sample(db, uid, WellnessSampleIn(
        date=day, source="oura", hrv_ms=hrv, sleep_hours=7.0, resting_hr=50.0))


async def _plain_user(db: AsyncSession, email: str) -> int:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    return user.id


@pytest.mark.parametrize("newer_hook_ran_first", [True, False])
async def test_a_delayed_snapshot_never_supersedes_newer_committed_inputs(
    async_db, monkeypatch, newer_hook_ran_first
):
    uid = await _plain_user(async_db, f"p7-stale-{newer_hook_ran_first}@test.com")
    v1 = await _sample(async_db, uid, 60.0)
    ref, old = sample_ref(v1), WellnessTelemetrySnapshot.from_sample(v1)
    v2 = await _sample(async_db, uid, 72.0)  # the same row, corrected in place
    new = WellnessTelemetrySnapshot.from_sample(v2)
    if newer_hook_ran_first:
        assert await recovery_shadow_service.record_recovery_shadow(
            async_db, ref, new, as_of=AS_OF) == "recorded"

    # A stale hook stops at its early check: it reads no state and builds no history frame.
    computed = []

    async def _no_state(*args, **kwargs):
        computed.append("state")

    async def _no_frame(*args, **kwargs):
        computed.append("frame")

    with monkeypatch.context() as m:
        m.setattr(recovery_shadow_service, "load_current_state", _no_state)
        m.setattr(personalization_shadow_service, "_build_recovery_frame", _no_frame)
        assert await recovery_shadow_service.record_recovery_shadow(
            async_db, ref, old, as_of=AS_OF) == "stale"
        assert await personalization_shadow_service.record_personalization_shadow(
            async_db, ref, old) == "stale"
    assert computed == []
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.content_hash for r in rows] == ([inputs_hash(new)] if newer_hook_ran_first else [])

    # The newer write's own hook (late or not) always lands, as the head.
    await recovery_shadow_service.record_recovery_shadow(async_db, ref, new, as_of=AS_OF)
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert len(rows) == 1 and rows[0].content_hash == inputs_hash(new)


def _pause_first(monkeypatch, target, name: str) -> tuple[asyncio.Event, asyncio.Event]:
    """Pause the first call of ``target.name`` (after it returns) until released."""
    real = getattr(target, name)
    paused, release = asyncio.Event(), asyncio.Event()
    first = {"call": True}

    async def _wrapper(*args, **kwargs):
        result = await real(*args, **kwargs)
        if first["call"]:
            first["call"] = False
            paused.set()
            await release.wait()
        return result

    monkeypatch.setattr(target, name, _wrapper)
    return paused, release


async def _current_recovery(db: AsyncSession, uid: int):
    db.expire_all()
    return (await shadow_summary_service.athlete_shadow_summary(db, uid))["recovery"]


async def test_hooks_for_one_sample_run_one_at_a_time_and_a_changed_source_publishes_nothing(
    async_db, monkeypatch
):
    """Hook 1 has checked v1 and is paused; v2 commits and hook 2 starts. Hook 2 waits for
    hook 1 (the per-sample lock). Hook 1's publication check then sees v2 and writes nothing,
    and hook 2 records v2."""
    uid = await _plain_user(async_db, "p7-interleave@test.com")
    v1 = await _sample(async_db, uid, 60.0)
    ref, old = sample_ref(v1), WellnessTelemetrySnapshot.from_sample(v1)
    paused, release = _pause_first(monkeypatch, recovery_shadow_service, "lock_current_inputs")
    hook1 = asyncio.create_task(
        recovery_shadow_service.record_recovery_shadow(async_db, ref, old, as_of=AS_OF))
    await paused.wait()

    v2 = await _sample(async_db, uid, 72.0)
    new = WellnessTelemetrySnapshot.from_sample(v2)
    hook2 = asyncio.create_task(
        recovery_shadow_service.record_recovery_shadow(async_db, ref, new, as_of=AS_OF))
    await asyncio.sleep(1.0)  # ample time for hook 2 to finish, were it not blocked
    assert not hook2.done()
    release.set()
    assert await hook1 == "stale" and await hook2 == "recorded"
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.content_hash for r in rows] == [inputs_hash(new)]


async def test_a_change_after_the_early_check_publishes_nothing_even_if_the_newer_hook_fails(
    async_db, monkeypatch
):
    """The review's reproduction: hook 1 passes its early check on HRV 60 and pauses; HRV 72
    commits; the newer hook never runs (it failed). Hook 1 must not publish 60."""
    uid = await _plain_user(async_db, "p7-early-check@test.com")
    v1 = await _sample(async_db, uid, 60.0)
    ref, old = sample_ref(v1), WellnessTelemetrySnapshot.from_sample(v1)
    paused, release = _pause_first(monkeypatch, recovery_shadow_service, "lock_current_inputs")
    hook1 = asyncio.create_task(
        recovery_shadow_service.record_recovery_shadow(async_db, ref, old, as_of=AS_OF))
    await paused.wait()
    await _sample(async_db, uid, 72.0)
    release.set()
    assert await hook1 == "stale"
    assert await _rows(async_db, RecoveryShadowLog, uid) == []
    assert await _current_recovery(async_db, uid) is None


async def test_publication_holds_the_source_and_a_row_it_outlives_stops_counting(
    async_db, monkeypatch
):
    """Hook 1 holds v1 FOR SHARE at publication: the v2 update waits for it to commit. v2's own
    hook then fails, so the v1 row is the head; the reader rejects it, since its inputs are no
    longer the sample's."""
    uid = await _plain_user(async_db, "p7-share@test.com")
    v1 = await _sample(async_db, uid, 60.0)
    ref, old = sample_ref(v1), WellnessTelemetrySnapshot.from_sample(v1)
    paused, release = _pause_first(
        monkeypatch, wellness_shadow_chain.AthleteContextRepository, "lock_wellness_inputs")
    hook1 = asyncio.create_task(
        recovery_shadow_service.record_recovery_shadow(async_db, ref, old, as_of=AS_OF))
    await paused.wait()

    update = asyncio.create_task(_sample(async_db, uid, 72.0))
    await asyncio.sleep(1.0)  # ample time for the update to commit, were it not blocked
    assert not update.done()
    release.set()
    assert await hook1 == "recorded"
    await update
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.content_hash for r in rows] == [inputs_hash(old)]
    assert await _current_recovery(async_db, uid) is None


async def test_a_skip_under_another_model_version_retires_the_computation(
    http_client, async_db, monkeypatch
):
    """The review's reproduction: computed under the committed artifact, the artifact removed,
    the sample corrected the next day: a skip in the "none" chain. The computation must stop
    counting, and so must a legacy unlinked computation."""
    login, uid = await _athlete(http_client, async_db, "p7-versions@test.com")
    async_db.add(RecoveryShadowLog(  # a pre-P7 computation: unlinked
        user_id=uid, model_version="legacy", wellness={}, fatigue_before={},
        baseline_clearance_multiplier={"f_met_systemic": 1.0},
        learned_clearance_multiplier={"f_met_systemic": 1.0}))
    await async_db.commit()
    assert await _current_recovery(async_db, uid) is not None

    await _checkin(http_client, login, TODAY)
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert rows[-1].decision_impact == RECORDED and rows[-1].model_version != "none"
    assert (await _current_recovery(async_db, uid))["model_version"] == rows[-1].model_version

    monkeypatch.setattr(recovery_shadow_service, "load_namespace_override", lambda ns: None)
    monkeypatch.setattr(wellness_shadow_hooks, "_now", lambda: AS_OF + timedelta(days=1))
    await _checkin(http_client, login, TODAY, hrv_ms=75.0)
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert (rows[-1].model_version, rows[-1].decision_impact) == ("none", SKIPPED_NOT_AS_OF_DAY)
    assert rows[-1].supersedes_id is None  # its own chain: nothing links it to v1's row
    assert await _current_recovery(async_db, uid) is None


async def test_a_skip_gets_its_computation_once_its_day_comes_and_only_then(async_db):
    uid = await _plain_user(async_db, "p7-future@test.com")
    tomorrow = TODAY + timedelta(days=1)
    sample = await _sample(async_db, uid, 60.0, day=tomorrow)  # synced ahead of its day
    ref, snap = sample_ref(sample), WellnessTelemetrySnapshot.from_sample(sample)

    async def _run(days: int) -> str:
        return await recovery_shadow_service.record_recovery_shadow(
            async_db, ref, snap, as_of=AS_OF + timedelta(days=days))

    assert await _run(0) == SKIPPED_NOT_AS_OF_DAY
    assert await _run(0) == "unchanged"     # still not its day
    assert await _run(1) == "recorded"      # its day: skipped → computed
    assert await _run(1) == "unchanged"
    assert await _run(2) == "unchanged"     # the clock moving on appends nothing
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.decision_impact for r in rows] == [SKIPPED_NOT_AS_OF_DAY, RECORDED]
    assert rows[1].supersedes_id == rows[0].id
    assert (await _current_recovery(async_db, uid)) is not None


async def test_concurrent_hooks_for_one_sample_write_one_row(async_db):
    uid = await _plain_user(async_db, "p7-race@test.com")
    sample = await _sample(async_db, uid, 60.0)
    ref, snap = sample_ref(sample), WellnessTelemetrySnapshot.from_sample(sample)
    # Each call opens its own session on the pool: two real connections, contending.
    outcomes = await asyncio.gather(*(
        recovery_shadow_service.record_recovery_shadow(async_db, ref, snap, as_of=AS_OF)
        for _ in range(2)
    ))
    assert sorted(outcomes) == ["recorded", "unchanged"]
    assert len(await _rows(async_db, RecoveryShadowLog, uid)) == 1


async def test_a_key_conflict_is_reread_not_lost(async_db, monkeypatch):
    uid = await _plain_user(async_db, "p7-conflict@test.com")
    sample = await _sample(async_db, uid, 60.0)
    ref, snap = sample_ref(sample), WellnessTelemetrySnapshot.from_sample(sample)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "recorded"

    # Make the next two head reads miss the existing root, as a reader that raced a commit
    # would: the insert then collides with uq_recovery_shadow_chain_root and must re-read.
    real_head = wellness_shadow_chain._head
    misses = {"left": 2}

    async def _stale_head(*args, **kwargs):
        if misses["left"]:
            misses["left"] -= 1
            return None
        return await real_head(*args, **kwargs)

    monkeypatch.setattr(wellness_shadow_chain, "_head", _stale_head)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "unchanged"
    assert misses["left"] == 0
    count = (await async_db.execute(
        select(func.count()).select_from(RecoveryShadowLog).where(RecoveryShadowLog.user_id == uid)
    )).scalar_one()
    assert count == 1
