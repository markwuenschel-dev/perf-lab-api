"""INT-15 S3 — shadows skip damaged state and never reconstruct it.

Shadows are neither display nor decisions. They compute a counterfactual from the athlete's
current state, so a legacy reconstruction would feed them a vector nobody observed; and a decode
failure is not theirs to turn into a refusal. `try_load_current_state_for_shadow` therefore
returns canonical state or a typed skip:

1. Every damaged head maps to its skip outcome, through the same taxonomy as the 409 refusals.
2. "No state row" stays None — each shadow's existing no-op — and is not a skip.
3. The legacy projectors are never reached, on any shadow entry point.
4. EKF: a skip writes NO ekf row (the newest row is the chain head) and logs the reason.
5. Recovery: a skip writes a skip row naming the reason, once; a repaired head then computes.
6. The wellness check-in still commits and returns 200 (its write commits before the shadows,
   which run in their own transactions), and the head is left untouched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import state_bridge, state_loading
from app.engine.engine_state_codec import payload_hash
from app.engine.state_bridge import default_engine_state_dict
from app.logic.ekf.wellness_input import build_wellness_shadow_input
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.athlete_state import AthleteState
from app.models.ekf_shadow import EkfShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import (
    ekf_shadow_service,
    recovery_shadow_service,
    state_service,
    wellness_shadow_hooks,
)
from app.services.ekf_shadow_service import MappingSpec
from app.services.shadow_evidence import RECORDED, latest_current_evidence
from app.services.shadow_summary_service import athlete_shadow_summary
from app.services.state_service import initialize_athlete_state, try_load_current_state_for_shadow
from app.services.wellness_shadow_hooks import sample_ref

pytestmark = pytest.mark.asyncio

AS_OF = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
TODAY = AS_OF.date()

# Every way a head can exist without canonical state, with the skip each must become. Healthy
# legacy scalars sit beside each, so a permissive loader would "succeed" on all of them.
DAMAGED = [
    pytest.param(None, "skipped_missing", id="null-legacy-row"),
    pytest.param({"version": 2, "x": {}, "f": {}, "t": {}}, "skipped_incomplete", id="empty-vectors"),
    pytest.param({"version": 2, "x": {"aerobic": 1.0}}, "skipped_incomplete", id="missing-vectors"),
    pytest.param({"version": 2, "x": "not-an-object", "f": {}, "t": {}}, "skipped_malformed",
                 id="malformed"),
    pytest.param({"version": 99, "x": {}, "f": {}, "t": {}}, "skipped_future_version",
                 id="future-version"),
]


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wellness_shadow_hooks, "_now", lambda: AS_OF)


@pytest.fixture
def legacy_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every call to a legacy projector, permissive or display. Recording, not raising:
    a raise inside a shadow's best-effort write would be swallowed and read as a pass."""
    calls: list[str] = []

    def spy(module: Any, name: str) -> None:
        real = getattr(module, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            calls.append(f"{module.__name__}.{name}")
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapped)

    for name in ("capacity_from_legacy", "fatigue_from_legacy", "tissue_from_legacy",
                 "unified_from_athlete_row"):
        spy(state_bridge, name)
    for name in ("capacity_from_legacy", "fatigue_from_legacy", "tissue_from_legacy",
                 "reconstruct_legacy_state_for_display"):
        spy(state_loading, name)
    for name in ("unified_from_athlete_row", "reconstruct_legacy_state_for_display"):
        spy(state_service, name)
    return calls


async def _user(db: AsyncSession, email: str) -> int:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    return user.id


async def _head(db: AsyncSession, user_id: int, payload: Any, ts: datetime | None = None) -> int:
    row = AthleteState(
        user_id=user_id,
        timestamp=ts or datetime(2026, 7, 1, 12, 0),
        c_met_aerobic=300.0, c_nm_force=1400.0, c_struct=100.0, b_met_anaerobic=15000.0,
        f_met_systemic=5.0, f_nm_peripheral=5.0, f_nm_central=5.0, f_struct_damage=5.0,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={},
        engine_state=payload,
    )
    db.add(row)
    await db.commit()
    return row.id


async def _payload_hash(db: AsyncSession, row_id: int) -> str:
    db.expire_all()
    row = (await db.execute(select(AthleteState).where(AthleteState.id == row_id))).scalar_one()
    return payload_hash(row.engine_state)


async def _rows(db: AsyncSession, model: Any, user_id: int) -> list[Any]:
    db.expire_all()
    return list((await db.execute(
        select(model).where(model.user_id == user_id).order_by(model.id)
    )).scalars().all())


async def _sample(db: AsyncSession, user_id: int, soreness: float = 6.0) -> WellnessSample:
    sample = WellnessSample(user_id=user_id, date=TODAY, source="manual", soreness=soreness,
                            sleep_hours=7.0, hrv_ms=60.0, resting_hr=52.0, mood=6.0)
    db.add(sample)
    await db.commit()
    await db.refresh(sample)
    return sample


# --- 1-2. the loader ----------------------------------------------------------------------


@pytest.mark.parametrize(("payload", "skip"), DAMAGED)
async def test_every_damaged_head_becomes_its_skip_and_nothing_is_reconstructed(
    async_db: AsyncSession, legacy_calls: list[str], payload: Any, skip: str
) -> None:
    uid = await _user(async_db, f"s3-load-{skip}-{id(payload)}@t.io")
    head = await _head(async_db, uid, payload)
    before = await _payload_hash(async_db, head)

    loaded = await try_load_current_state_for_shadow(async_db, uid)

    assert loaded is not None
    assert (loaded.outcome, loaded.state) == (skip, None)
    assert legacy_calls == []
    assert await _payload_hash(async_db, head) == before


async def test_no_state_row_is_none_not_a_skip(async_db: AsyncSession) -> None:
    uid = await _user(async_db, "s3-none@t.io")
    assert await try_load_current_state_for_shadow(async_db, uid) is None


async def test_a_canonical_head_loads(async_db: AsyncSession, legacy_calls: list[str]) -> None:
    uid = await _user(async_db, "s3-canonical@t.io")
    await _head(async_db, uid, default_engine_state_dict())
    loaded = await try_load_current_state_for_shadow(async_db, uid)
    assert loaded is not None and loaded.outcome == "loaded" and loaded.state is not None
    assert legacy_calls == []


# --- 3-4. EKF -----------------------------------------------------------------------------


@pytest.mark.parametrize(("payload", "skip"), DAMAGED)
async def test_ekf_predict_and_update_skip_without_writing_a_chain_row(
    async_db: AsyncSession, legacy_calls: list[str], caplog: pytest.LogCaptureFixture,
    payload: Any, skip: str,
) -> None:
    uid = await _user(async_db, f"s3-ekf-{skip}-{id(payload)}@t.io")
    head = await _head(async_db, uid, payload)
    before = await _payload_hash(async_db, head)
    log = WorkoutLog(timestamp=datetime(2026, 7, 2, 12, 0), modality="Strength",
                     duration_minutes=60.0, session_rpe=7.0, sleep_quality=7.0,
                     life_stress_inverse=7.0)

    with caplog.at_level(logging.INFO, logger=ekf_shadow_service.__name__):
        await ekf_shadow_service.record_ekf_predict(
            async_db, uid, StressDose(), timedelta(hours=24), log
        )
        await ekf_shadow_service.record_ekf_update(
            async_db, uid, benchmark_code="1rm",
            mapping_specs=[MappingSpec(target_vector="capacity", target_key="max_strength",
                                       coefficient=1.0)],
            score01=0.85, observed_at=datetime.now(UTC),
        )

    assert await _rows(async_db, EkfShadowLog, uid) == []
    skipped = [r.getMessage() for r in caplog.records if "ekf_shadow_skipped" in r.getMessage()]
    assert skipped == [
        f"ekf_shadow_skipped entry=predict outcome={skip} user_id={uid}",
        f"ekf_shadow_skipped entry=update outcome={skip} user_id={uid}",
    ]
    assert not any("telemetry write failed" in r.getMessage() for r in caplog.records)
    assert legacy_calls == []
    assert await _payload_hash(async_db, head) == before


# --- 5-6. the wellness check-in: recovery + EKF wellness, production unaffected ------------


async def _athlete(client: AsyncClient, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


@pytest.mark.parametrize(("payload", "skip"), DAMAGED)
async def test_a_check_in_over_damaged_state_succeeds_and_each_shadow_skips(
    http_client: AsyncClient, async_db: AsyncSession, legacy_calls: list[str],
    payload: Any, skip: str,
) -> None:
    login, uid = await _athlete(http_client, async_db, f"s3-route-{skip}-{id(payload)}@t.io")
    head = await _head(async_db, uid, payload)
    before = await _payload_hash(async_db, head)

    r = await http_client.post("/v1/wellness", headers=login, json={
        "date": TODAY.isoformat(), "source": "manual", "soreness": 6.0, "sleep_hours": 7.0,
        "hrv_ms": 60.0, "resting_hr": 52.0, "mood": 6.0,
    })

    assert r.status_code == 200, r.text  # committed before the shadows; a skip is no refusal
    sample_ids = [s.id for s in await _rows(async_db, WellnessSample, uid)]
    assert sample_ids == [r.json()["id"]]
    [recovery] = await _rows(async_db, RecoveryShadowLog, uid)
    assert recovery.decision_impact == skip
    assert recovery.source_wellness_sample_id == sample_ids[0]
    assert recovery.fatigue_before == {} and recovery.baseline_clearance_multiplier == {}
    assert recovery.learned_clearance_multiplier == {}
    assert await _rows(async_db, EkfShadowLog, uid) == []
    assert legacy_calls == []
    assert await _payload_hash(async_db, head) == before


@pytest.mark.parametrize(("payload", "skip"), DAMAGED)
async def test_the_shadows_report_their_skips(
    async_db: AsyncSession, payload: Any, skip: str
) -> None:
    uid = await _user(async_db, f"s3-outcomes-{skip}-{id(payload)}@t.io")
    await _head(async_db, uid, payload)
    sample = await _sample(async_db, uid)

    outcomes = await wellness_shadow_hooks.run_wellness_shadows(
        async_db, sample, assimilate_soreness=True
    )

    assert (outcomes.recovery, outcomes.ekf) == (skip, "skipped")


async def test_a_recovery_skip_is_written_once_and_a_repaired_head_computes(
    async_db: AsyncSession,
) -> None:
    uid = await _user(async_db, "s3-repair@t.io")
    await _head(async_db, uid, {"version": 2, "x": {}, "f": {}, "t": {}})
    sample = await _sample(async_db, uid)
    ref, snap = sample_ref(sample), WellnessTelemetrySnapshot.from_sample(sample)

    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "skipped_incomplete"
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "unchanged"  # same inputs, same damage: no new row
    assert len(await _rows(async_db, RecoveryShadowLog, uid)) == 1

    await _head(async_db, uid, default_engine_state_dict(), ts=datetime(2026, 7, 2, 12, 0))
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "recorded"
    skip_row, computed = await _rows(async_db, RecoveryShadowLog, uid)
    assert computed.decision_impact == RECORDED and computed.supersedes_id == skip_row.id
    assert computed.fatigue_before and computed.baseline_clearance_multiplier


async def test_over_canonical_state_the_shadows_still_compute(
    async_db: AsyncSession, legacy_calls: list[str]
) -> None:
    uid = await _user(async_db, "s3-happy@t.io")
    await initialize_athlete_state(async_db, uid)
    sample = await _sample(async_db, uid)
    sample_id = sample.id
    legacy_calls.clear()  # seeding decodes the baseline it just built; only the shadows count

    outcomes = await wellness_shadow_hooks.run_wellness_shadows(
        async_db, sample, assimilate_soreness=True
    )

    assert (outcomes.recovery, outcomes.ekf) == ("recorded", "assimilated")
    [recovery] = await _rows(async_db, RecoveryShadowLog, uid)
    assert recovery.decision_impact == RECORDED and recovery.fatigue_before
    [ekf] = await _rows(async_db, EkfShadowLog, uid)
    assert ekf.event_type == "update" and ekf.source_wellness_sample_id == sample_id
    assert legacy_calls == []


async def test_a_skip_is_not_evidence_and_retires_the_computation_it_replaces(
    async_db: AsyncSession,
) -> None:
    """A corrected check-in over now-damaged state: the skip supersedes the earlier computed row,
    and the positive evidence reader then shows nothing rather than the stale computation."""
    uid = await _user(async_db, "s3-evidence@t.io")
    await initialize_athlete_state(async_db, uid)
    sample = await _sample(async_db, uid, soreness=3.0)
    ref = sample_ref(sample)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, WellnessTelemetrySnapshot.from_sample(sample), as_of=AS_OF) == "recorded"
    assert await latest_current_evidence(async_db, RecoveryShadowLog, uid) is not None

    await _head(async_db, uid, None, ts=datetime(2030, 1, 1, 12, 0))  # newer, damaged head
    sample.soreness = 8.0
    await async_db.commit()
    await async_db.refresh(sample)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, WellnessTelemetrySnapshot.from_sample(sample), as_of=AS_OF
    ) == "skipped_missing"

    computed, skip_row = await _rows(async_db, RecoveryShadowLog, uid)
    assert skip_row.supersedes_id == computed.id and skip_row.decision_impact == "skipped_missing"
    assert await latest_current_evidence(async_db, RecoveryShadowLog, uid) is None


# --- owner requirement: state is classified BEFORE the unchanged-input shortcut -------------


async def test_unchanged_input_over_a_head_damaged_since_supersedes_then_resumes(
    async_db: AsyncSession, legacy_calls: list[str],
) -> None:
    """computed -> head damaged, same wellness input -> explicit skip supersedes the computation
    -> head repaired -> computation resumes. The shortcut must not hide the damage."""
    uid = await _user(async_db, "s3-unchanged@t.io")
    await _head(async_db, uid, default_engine_state_dict(), ts=datetime(2026, 7, 1, 12, 0))
    sample = await _sample(async_db, uid)
    ref, snap = sample_ref(sample), WellnessTelemetrySnapshot.from_sample(sample)

    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "recorded"
    [computed] = await _rows(async_db, RecoveryShadowLog, uid)
    computed_id = computed.id

    await _head(async_db, uid, {"version": 2, "x": "not-an-object", "f": {}, "t": {}},
                ts=datetime(2026, 7, 2, 12, 0))
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "skipped_malformed"  # same input, new damage
    _, skip_row = await _rows(async_db, RecoveryShadowLog, uid)
    skip_id = skip_row.id
    assert (skip_row.supersedes_id, skip_row.decision_impact) == (computed_id, "skipped_malformed")
    assert await latest_current_evidence(async_db, RecoveryShadowLog, uid) is None
    assert (await athlete_shadow_summary(async_db, uid))["recovery"] is None
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "unchanged"  # the skip itself is not rewritten

    await _head(async_db, uid, default_engine_state_dict(), ts=datetime(2026, 7, 3, 12, 0))
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, ref, snap, as_of=AS_OF) == "recorded"
    rows = await _rows(async_db, RecoveryShadowLog, uid)
    assert [r.decision_impact for r in rows] == [RECORDED, "skipped_malformed", RECORDED]
    assert rows[2].supersedes_id == skip_id
    current = await latest_current_evidence(async_db, RecoveryShadowLog, uid)
    assert current is not None and current.id == rows[2].id
    assert (await athlete_shadow_summary(async_db, uid))["recovery"] is not None
    assert legacy_calls == []


async def test_a_state_skip_hides_an_earlier_days_computation_too(async_db: AsyncSession) -> None:
    """The evidence reader walks back past a retired row to the newest one that counts. While a
    damaged-state skip is the athlete's newest recovery row, yesterday's computation must not be
    shown as current in its place."""
    uid = await _user(async_db, "s3-yesterday@t.io")
    await _head(async_db, uid, default_engine_state_dict())
    yesterday = WellnessSample(user_id=uid, date=TODAY - timedelta(days=1), source="manual",
                               soreness=4.0, sleep_hours=7.0, hrv_ms=60.0, resting_hr=52.0,
                               mood=6.0)
    async_db.add(yesterday)
    await async_db.commit()
    await async_db.refresh(yesterday)
    as_of_yesterday = AS_OF - timedelta(days=1)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, sample_ref(yesterday), WellnessTelemetrySnapshot.from_sample(yesterday),
        as_of=as_of_yesterday) == "recorded"
    assert await latest_current_evidence(async_db, RecoveryShadowLog, uid) is not None

    await _head(async_db, uid, None, ts=datetime(2026, 7, 2, 12, 0))
    today = await _sample(async_db, uid)
    assert await recovery_shadow_service.record_recovery_shadow(
        async_db, sample_ref(today), WellnessTelemetrySnapshot.from_sample(today), as_of=AS_OF
    ) == "skipped_missing"

    assert await latest_current_evidence(async_db, RecoveryShadowLog, uid) is None
    assert (await athlete_shadow_summary(async_db, uid))["recovery"] is None


async def test_the_ekf_exact_retry_over_a_since_damaged_head_writes_nothing(
    async_db: AsyncSession, legacy_calls: list[str],
) -> None:
    """The EKF's unchanged-input shortcut is its exact-retry classification. Its rows are a
    belief chain, not per-sample evidence of the athlete's current state, and a skip row would
    become the chain head; so a retry over now-damaged state adds nothing and reads no state."""
    uid = await _user(async_db, "s3-ekf-retry@t.io")
    await _head(async_db, uid, default_engine_state_dict())
    sample = await _sample(async_db, uid, soreness=7.0)
    si = build_wellness_shadow_input(uid, sample.id, 7.0)
    assert await ekf_shadow_service.record_ekf_wellness_observation(
        async_db, uid, si, observed_at=AS_OF) == "assimilated"
    before = [(r.id, r.event_type) for r in await _rows(async_db, EkfShadowLog, uid)]
    summary_before = (await athlete_shadow_summary(async_db, uid))["ekf"]

    await _head(async_db, uid, None, ts=datetime(2026, 7, 2, 12, 0))
    assert await ekf_shadow_service.record_ekf_wellness_observation(
        async_db, uid, si, observed_at=AS_OF) == "exact_retry"

    assert [(r.id, r.event_type) for r in await _rows(async_db, EkfShadowLog, uid)] == before
    assert (await athlete_shadow_summary(async_db, uid))["ekf"] == summary_before
    assert legacy_calls == []
