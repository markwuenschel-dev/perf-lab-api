"""P4-3b: a reported set whose effort is unknown or conflicting is a saved report and derives
nothing; standalone submissions can carry an idempotency key; a report is superseded only by
name.

* Classification is the shared resolver's (``resolve_effort``) and applies only to ``rep_set``:
  no RPE and no RIR → ``effort_unknown``; both, further apart than its tolerance →
  ``effort_conflict``. Such a report writes an ``underived_strength_reports`` row and nothing
  else — no observation, no projection, no seed, no state.
* Onboarding classifies before seeding, so an underived report seeds nothing, and its retry guard
  searches both stores by what was submitted (a legacy row is never reclassified).
* A keyed standalone submission commits its key with its effect. The same key and payload return
  the first outcome and repeat nothing; a changed payload is 422 with no writes.
* ``supersedes_report_id``: the athlete's own, unsuperseded report, superseded only by the same
  performance (lift, load, reps, performance time) with an effort that yields an estimate; the
  link commits with the observation. The list shows active reports; a superseded one stays
  readable by id.

Durability is asserted through sessions that did not make the writes (``_committed``):
``http_client`` shares one session across requests.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from conftest import TEST_DATABASE_URL, _truncate_all
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.strength_report import StrengthSubmissionKey, UnderivedStrengthReport
from app.models.user import AthleteProfile, User
from app.schemas.benchmarks import StrengthEvidenceCreate
from app.services import benchmark_service
from app.services import strength_evidence_service as ses

pytestmark = pytest.mark.asyncio

SQUAT, BENCH, DEADLIFT = "pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"
_LIFTS = {
    SQUAT: ("Back Squat", "squat"),
    BENCH: ("Bench Press", "push_horizontal"),
    DEADLIFT: ("Conventional Deadlift", "hinge"),
}
_NOW = datetime.now(UTC).replace(microsecond=0)


def _ago(days: float) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


def _naive_ago(days: float) -> datetime:
    return (_NOW - timedelta(days=days)).replace(tzinfo=None)


async def _catalog(db: AsyncSession) -> None:
    for code, (name, pattern) in _LIFTS.items():
        db.add(Exercise(
            name=name, modality="Strength", movement_pattern=pattern, load_type="barbell",
            is_benchmark=True, e1rm_benchmark_code=code,
        ))
        definition = BenchmarkDefinition(
            code=code, name=f"{name} e1RM", domain="powerlifting", metric_type="load", unit="kg",
            better_direction="higher", observation_weight=1.0,
            standardization_rules={"floor": 20.0, "cap": 320.0},
        )
        db.add(definition)
        await db.flush()
        db.add(ObservationMapping(
            benchmark_definition_id=definition.id, target_vector="capacity",
            target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
        ))
    await db.commit()


async def _athlete(client, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, user_id


async def _committed(db: AsyncSession, stmt: Any) -> Any:
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as fresh:
        return (await fresh.execute(stmt)).scalar_one()


def _count(model: Any, user_id: int) -> Any:
    return select(func.count()).select_from(model).where(model.user_id == user_id)


def _squat_value(user_id: int) -> Any:
    return select(AthleteProfile.squat_1rm).where(AthleteProfile.user_id == user_id)


def _set(code: str = SQUAT, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "benchmark_code": code, "method": "rep_set", "load_kg": 100.0, "reps": 5,
        "performed_at": _ago(1),
    }
    body.update(over)
    return body


async def _post(client, headers, body: dict[str, Any]):
    return await client.post("/v1/benchmarks/strength-evidence", json=body, headers=headers)


# ── classification ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("effort", "reason"), [
    ({}, "effort_unknown"),
    ({"rpe": 8.0, "rir": 0.0}, "effort_conflict"),
])
async def test_an_underived_set_is_a_report_and_changes_nothing_else(
    http_client, async_db, monkeypatch, effort, reason
):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, f"u-{reason}@test.com")
    completed: list[Any] = []
    real_complete = benchmark_service.complete_observation

    async def counting(db, uid, staged):
        completed.append(staged)
        return await real_complete(db, uid, staged)

    monkeypatch.setattr(benchmark_service, "complete_observation", counting)

    resp = await _post(http_client, headers, _set(**effort))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "report_only" and body["reason"] == reason
    assert body["value_available"] is False and body["observation"] is None
    report = body["report"]
    assert (report["load_kg"], report["reps"], report["reason"]) == (100.0, 5, reason)
    assert report["rpe"] == effort.get("rpe") and report["rir"] == effort.get("rir")

    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 1
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0
    assert await _committed(async_db, _count(AthleteState, user_id)) == 0
    assert await _committed(async_db, _squat_value(user_id)) is None
    assert completed == []  # no shadows, no KPI recompute


# RPE 8 ≡ RIR 2. The resolver's tolerance is 0.5: 1.5 is the accepted boundary, 1.49 is past it.
_BOUNDARIES = [
    pytest.param(2.0, "recorded", id="8-2-agree"),
    pytest.param(1.5, "recorded", id="8-1.5-boundary-accepted"),
    pytest.param(1.49, "report_only", id="8-1.49-conflict"),
]


@pytest.mark.parametrize(("rir", "outcome"), _BOUNDARIES)
async def test_the_conflict_boundary_through_the_standalone_api(http_client, async_db, rir, outcome):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, f"b-solo-{rir}@test.com")
    resp = await _post(http_client, headers, _set(reps=3, rpe=8.0, rir=rir))
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == outcome
    if outcome == "report_only":
        assert resp.json()["reason"] == "effort_conflict"


@pytest.mark.parametrize(("rir", "outcome"), _BOUNDARIES)
async def test_the_conflict_boundary_through_onboarding(http_client, async_db, rir, outcome):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, f"b-onb-{rir}@test.com")
    resp = await http_client.post("/v1/onboard", headers=headers, json={
        "strength": [_set(BENCH, reps=3, rpe=8.0, rir=rir)],
    })
    assert resp.status_code == 200, resp.text
    [only] = resp.json()["strength"]
    assert only["outcome"] == outcome
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == (
        1 if outcome == "report_only" else 0
    )


async def test_tested_max_and_estimate_are_never_classified(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-controls@test.com")
    tested = await _post(http_client, headers, {
        "benchmark_code": SQUAT, "method": "tested_max", "value_kg": 150.0, "performed_at": _ago(1),
    })
    estimate = await _post(http_client, headers, {
        "benchmark_code": BENCH, "method": "estimate", "value_kg": 100.0,
    })
    assert tested.json()["outcome"] == estimate.json()["outcome"] == "recorded"
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 0
    assert await _committed(async_db, _squat_value(user_id)) == 150.0


# ── onboarding ───────────────────────────────────────────────────────────────────


def _capacities(state: AthleteState) -> tuple[float, ...]:
    return (state.c_met_aerobic, state.c_nm_force, state.c_struct, state.b_met_anaerobic)


async def test_onboarding_seeds_nothing_from_an_underived_report(http_client, async_db):
    """A fresh athlete gets the same provisional baseline as one who gave no report; the tested
    max and the estimate beside it are recorded and seed as before."""
    await _catalog(async_db)
    basics = {"experience_level": "intermediate", "bodyweight_kg": 80.0}
    h_none, without = await _athlete(http_client, async_db, "onb-none@test.com")
    assert (await http_client.post("/v1/onboard", headers=h_none, json=basics)).status_code == 200
    h_set, with_set = await _athlete(http_client, async_db, "onb-set@test.com")
    resp = await http_client.post("/v1/onboard", headers=h_set, json={
        **basics, "strength": [_set(SQUAT)],
    })
    assert resp.status_code == 200, resp.text
    assert [o["outcome"] for o in resp.json()["strength"]] == ["report_only"]

    async with async_sessionmaker(async_db.bind, expire_on_commit=False)() as fresh:
        a = (await fresh.execute(select(AthleteState).where(AthleteState.user_id == without))).scalar_one()
        b = (await fresh.execute(select(AthleteState).where(AthleteState.user_id == with_set))).scalar_one()
    assert _capacities(a) == _capacities(b)
    assert await _committed(async_db, _squat_value(with_set)) is None

    h_mix, mixed = await _athlete(http_client, async_db, "onb-mixed@test.com")
    resp = await http_client.post("/v1/onboard", headers=h_mix, json={**basics, "strength": [
        _set(SQUAT),
        {"benchmark_code": BENCH, "method": "tested_max", "value_kg": 100.0, "performed_at": _ago(2)},
        {"benchmark_code": DEADLIFT, "method": "estimate", "value_kg": 180.0},
    ]})
    assert [o["outcome"] for o in resp.json()["strength"]] == ["report_only", "recorded", "recorded"]
    assert await _committed(async_db, _count(BenchmarkObservation, mixed)) == 2


async def test_an_underived_report_leaves_an_existing_profile_value(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-existing@test.com")
    await _post(http_client, headers, {
        "benchmark_code": SQUAT, "method": "tested_max", "value_kg": 150.0, "performed_at": _ago(3),
    })
    resp = await _post(http_client, headers, _set(load_kg=160.0))
    assert resp.json()["outcome"] == "report_only"
    assert await _committed(async_db, _squat_value(user_id)) == 150.0


async def test_onboarding_retried_with_an_underived_report_records_it_once(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-onb-retry@test.com")
    body = {"strength": [_set(SQUAT)]}
    first = (await http_client.post("/v1/onboard", headers=headers, json=body)).json()["strength"][0]
    second = (await http_client.post("/v1/onboard", headers=headers, json=body)).json()["strength"][0]
    assert first["outcome"] == "report_only"
    assert second["outcome"] == "already_recorded" and second["reason"] == "effort_unknown"
    assert second["report"]["id"] == first["report"]["id"] and second["value_available"] is False
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 1


async def _legacy_set(db: AsyncSession, user_id: int, *, days_ago: float, raw_value: float,
                      collection_mode: str = oa.CM_ONBOARDING_ONRAMP) -> int:
    """A set recorded as evidence before P4-3, with no effort (its value is Epley's)."""
    definition_id = (await db.execute(
        select(BenchmarkDefinition.id).where(BenchmarkDefinition.code == SQUAT)
    )).scalar_one()
    row = BenchmarkObservation(
        user_id=user_id, benchmark_definition_id=definition_id, observed_at=_naive_ago(days_ago),
        performed_at=_naive_ago(days_ago), raw_value=raw_value, validity_status="valid",
        source=se.SOURCE_MANUAL, source_type=oa.ST_ATHLETE_ENTRY, collection_mode=collection_mode,
        provenance_operation=oa.OP_STRENGTH_REPORT, evidence_type=se.EV_ESTIMATED_FROM_TRAINING_SET,
        value_semantics=se.VS_ESTIMATED, affects_prescription=True, reps=5, load_kg=100.0,
        formula="epley", effort_fidelity=se.FIDELITY_SET_LEVEL,
    )
    db.add(row)
    await db.commit()
    return row.id


async def test_onboarding_never_reclassifies_a_legacy_row(http_client, async_db):
    """A no-effort set onboarding recorded as evidence before P4-3, retried after it, is that
    evidence with its stored value — not a new report, and not relabelled as one. Seeding is
    decided apart, from the submission's own classification: it seeds nothing."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-legacy-retry@test.com")
    performed = _NOW - timedelta(days=1)
    legacy_id = await _legacy_set(async_db, user_id, days_ago=1, raw_value=116.7)
    resp = await http_client.post("/v1/onboard", headers=headers, json={
        "strength": [_set(SQUAT, performed_at=performed.isoformat())],
    })
    [only] = resp.json()["strength"]
    assert only["outcome"] == "already_recorded" and only["observation"]["id"] == legacy_id
    assert only["observation"]["raw_value"] == 116.7
    assert only["reason"] is None and only["value_available"] is True
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 0
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1
    async with async_sessionmaker(async_db.bind)() as fresh:
        squat = (await fresh.execute(_squat_value(user_id))).scalar_one_or_none()
    assert squat is None  # the retried submission is underived: it seeds and projects nothing


async def test_a_failure_after_an_underived_report_stages_commits_nothing(http_client, async_db, monkeypatch):
    """Mixed onboarding: the squat's report is staged, then the bench's evidence fails."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-onb-fail@test.com")

    async def fails(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected after the underived report staged")

    monkeypatch.setattr(benchmark_service, "stage_observation", fails)
    with pytest.raises(RuntimeError, match="injected"):
        await http_client.post("/v1/onboard", headers=headers, json={"strength": [
            _set(SQUAT),
            {"benchmark_code": BENCH, "method": "tested_max", "value_kg": 100.0, "performed_at": _ago(2)},
        ]})
    await async_db.rollback()
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 0
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0
    assert await _committed(async_db, _count(AthleteState, user_id)) == 0


# ── profile projection (fork 4 = L3) ─────────────────────────────────────────────


async def test_the_projection_skips_a_legacy_unsupported_set(http_client, async_db):
    """Tested max 11 days ago; a legacy no-effort set 5 days ago; a characterized set performed 6
    days ago, reported last. The projection picks the characterized set. The legacy row keeps its
    stored value (KPIs and estimated-PR tracking still read it)."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "u-l3@test.com")
    await _post(http_client, headers, {
        "benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(11),
    })
    legacy_id = await _legacy_set(async_db, user_id, days_ago=5, raw_value=116.7,
                                  collection_mode=oa.CM_RETEST)
    assert await _committed(async_db, _squat_value(user_id)) == 140.0  # nothing recomputed yet

    resp = await _post(http_client, headers, _set(load_kg=130.0, reps=3, rpe=9.0, performed_at=_ago(6)))
    assert resp.json()["outcome"] == "recorded"
    assert await _committed(async_db, _squat_value(user_id)) == resp.json()["observation"]["raw_value"]
    assert await _committed(async_db, select(BenchmarkObservation.raw_value).where(
        BenchmarkObservation.id == legacy_id
    )) == 116.7


# ── idempotency keys ─────────────────────────────────────────────────────────────


async def test_a_keyed_retry_after_a_lost_response_returns_the_first_outcome(http_client, async_db, monkeypatch):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-retry@test.com")
    key = str(uuid.uuid4())
    body = _set(reps=3, rpe=9.0, idempotency_key=key)
    first = await _post(http_client, headers, body)  # committed; the client never saw this
    assert first.status_code == 200, first.text

    calls: list[str] = []
    for name in ("stage_observation", "complete_observation"):
        real = getattr(benchmark_service, name)

        async def spy(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            calls.append(_name)
            return await _real(*args, **kwargs)

        monkeypatch.setattr(benchmark_service, name, spy)

    async def no_projection(*args: Any, **kwargs: Any) -> None:
        calls.append("projection")

    monkeypatch.setattr(ses, "_stage_projection", no_projection)
    again = await _post(http_client, headers, body)
    assert again.status_code == 200 and again.json() == first.json()
    assert calls == []  # nothing staged, projected or completed again
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1
    assert await _committed(async_db, _count(StrengthSubmissionKey, user_id)) == 1


async def test_a_keyed_underived_report_is_replayed_too(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-report@test.com")
    body = _set(idempotency_key=str(uuid.uuid4()))
    first, again = await _post(http_client, headers, body), await _post(http_client, headers, body)
    assert first.json()["outcome"] == "report_only" and again.json() == first.json()
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 1


async def test_a_reused_key_with_a_changed_payload_is_refused_and_writes_nothing(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-changed@test.com")
    key = str(uuid.uuid4())
    assert (await _post(http_client, headers, _set(reps=3, rpe=9.0, idempotency_key=key))).status_code == 200
    for changed in (
        _set(reps=3, rpe=9.0, load_kg=105.0, idempotency_key=key),
        _set(reps=3, rpe=9.0, performed_at=_ago(2), idempotency_key=key),
        _set(reps=3, rpe=9.0, collection_mode="onboarding_onramp", idempotency_key=key),
        {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 100.0,
         "performed_at": _ago(1), "idempotency_key": key},
        _set(reps=3, rpe=9.0, supersedes_report_id=1, idempotency_key=key),
    ):
        resp = await _post(http_client, headers, changed)
        assert resp.status_code == 422, resp.text
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1
    assert await _committed(async_db, _count(StrengthSubmissionKey, user_id)) == 1


async def test_one_instant_with_two_offsets_is_one_payload(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-offset@test.com")
    key = str(uuid.uuid4())
    utc = _NOW - timedelta(days=1)
    plus_two = utc.astimezone(timezone(timedelta(hours=2)))
    first = await _post(http_client, headers, _set(reps=3, rpe=9.0, performed_at=utc.isoformat(), idempotency_key=key))
    again = await _post(http_client, headers, _set(reps=3, rpe=9, performed_at=plus_two.isoformat(), idempotency_key=key))
    assert again.status_code == 200 and again.json() == first.json()
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1


async def test_distinct_keys_and_no_key_record_identical_sets(http_client, async_db):
    """Identical same-day sets are legitimate: only a reused key marks a retry."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-distinct@test.com")
    for key in (str(uuid.uuid4()), str(uuid.uuid4()), None, None):
        body = _set(reps=3, rpe=9.0) if key is None else _set(reps=3, rpe=9.0, idempotency_key=key)
        assert (await _post(http_client, headers, body)).json()["outcome"] == "recorded"
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 4
    assert await _committed(async_db, _count(StrengthSubmissionKey, user_id)) == 2


async def test_keys_are_per_athlete(http_client, async_db):
    await _catalog(async_db)
    key = str(uuid.uuid4())
    h1, u1 = await _athlete(http_client, async_db, "k-a@test.com")
    h2, u2 = await _athlete(http_client, async_db, "k-b@test.com")
    a = await _post(http_client, h1, _set(reps=3, rpe=9.0, idempotency_key=key))
    b = await _post(http_client, h2, _set(reps=3, rpe=9.0, idempotency_key=key))
    assert a.json()["outcome"] == b.json()["outcome"] == "recorded"
    assert a.json()["observation"]["id"] != b.json()["observation"]["id"]
    assert b.json()["observation"]["user_id"] == u2 and a.json()["observation"]["user_id"] == u1


async def test_a_keyed_submission_that_fails_before_commit_leaves_no_key(http_client, async_db, monkeypatch):
    """Rollback before commit: the key and its effect go together; a retry records once."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "k-rollback@test.com")
    real_stage_key = ses._stage_key

    async def stage_then_fail(*args: Any, **kwargs: Any) -> None:
        await real_stage_key(*args, **kwargs)
        raise RuntimeError("injected after the key staged")

    monkeypatch.setattr(ses, "_stage_key", stage_then_fail)
    body = _set(reps=3, rpe=9.0, idempotency_key=str(uuid.uuid4()))
    with pytest.raises(RuntimeError, match="injected"):
        await _post(http_client, headers, body)
    await async_db.rollback()
    assert await _committed(async_db, _count(StrengthSubmissionKey, user_id)) == 0
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0

    monkeypatch.setattr(ses, "_stage_key", real_stage_key)
    assert (await _post(http_client, headers, body)).json()["outcome"] == "recorded"
    assert (await _post(http_client, headers, body)).json()["outcome"] == "recorded"  # the replay
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1


async def test_a_key_refuses_to_name_another_athletes_record(async_db, monkeypatch):
    """The table does not check effect ownership; the writer does, before storing an outcome."""
    await _catalog(async_db)
    owner = User(email="k-owner@test.com", hashed_password="x", is_active=True)
    other = User(email="k-other@test.com", hashed_password="x", is_active=True)
    async_db.add_all([owner, other])
    await async_db.commit()
    owner_id, other_id = owner.id, other.id
    theirs = await ses.record_strength_report(
        async_db, owner_id, StrengthEvidenceCreate.model_validate(_set(reps=3, rpe=9.0)),
        collection_mode="retest",
    )
    with pytest.raises(RuntimeError, match="not the athlete's"):
        await ses._stage_key(async_db, other_id, uuid.uuid4(), {}, "d", theirs)
    await async_db.rollback()
    assert await _committed(async_db, _count(StrengthSubmissionKey, other_id)) == 0


@pytest_asyncio.fixture(loop_scope="function")
async def engine(_migrated_schema: None):
    eng = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    await _truncate_all(eng)
    yield eng
    await eng.dispose()


async def test_concurrent_requests_with_one_key_record_once(engine, monkeypatch):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        await _catalog(db)
        user = User(email="k-concurrent@test.com", hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        user_id = user.id

    real_projection = ses._stage_projection

    async def slow_projection(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(0.3)  # hold the first request open while the second arrives
        await real_projection(*args, **kwargs)

    monkeypatch.setattr(ses, "_stage_projection", slow_projection)
    body = StrengthEvidenceCreate.model_validate(_set(reps=3, rpe=9.0, idempotency_key=str(uuid.uuid4())))

    async def submit() -> Any:
        async with factory() as db:
            return await ses.record_strength_evidence(db, user_id, body)

    first, second = await asyncio.gather(submit(), submit())
    assert first == second
    async with factory() as db:
        assert await db.scalar(_count(BenchmarkObservation, user_id)) == 1
        assert await db.scalar(_count(StrengthSubmissionKey, user_id)) == 1


# ── supersession ─────────────────────────────────────────────────────────────────


async def _underived(client, headers) -> dict[str, Any]:
    resp = await _post(client, headers, _set())
    assert resp.json()["outcome"] == "report_only", resp.text
    return resp.json()["report"]


async def test_a_characterized_submission_supersedes_the_named_report(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "s-ok@test.com")
    report = await _underived(http_client, headers)
    resp = await _post(http_client, headers, _set(rpe=9.0, supersedes_report_id=report["id"]))
    assert resp.status_code == 200 and resp.json()["outcome"] == "recorded", resp.text
    shown = (await http_client.get(f"/v1/benchmarks/strength-reports/{report['id']}", headers=headers)).json()
    assert shown["superseded_by_observation_id"] == resp.json()["observation"]["id"]
    assert shown["superseded_at"] is not None


async def test_an_underived_submission_cannot_supersede(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "s-underived@test.com")
    report = await _underived(http_client, headers)
    resp = await _post(http_client, headers, _set(load_kg=110.0, supersedes_report_id=report["id"]))
    assert resp.status_code == 400, resp.text
    assert await _committed(async_db, _count(UnderivedStrengthReport, user_id)) == 1
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0


async def test_a_second_supersession_is_a_conflict_but_a_keyed_retry_is_not(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "s-twice@test.com")
    report = await _underived(http_client, headers)
    keyed = _set(rpe=9.0, supersedes_report_id=report["id"], idempotency_key=str(uuid.uuid4()))
    first = await _post(http_client, headers, keyed)
    assert first.status_code == 200, first.text

    other = await _post(http_client, headers, _set(rpe=9.5, supersedes_report_id=report["id"]))
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["superseded_by_observation_id"] == first.json()["observation"]["id"]

    retry = await _post(http_client, headers, keyed)  # the first response was lost
    assert retry.status_code == 200 and retry.json() == first.json()
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 1


async def test_another_athletes_report_is_not_found(http_client, async_db):
    await _catalog(async_db)
    h1, _ = await _athlete(http_client, async_db, "s-mine@test.com")
    h2, u2 = await _athlete(http_client, async_db, "s-theirs@test.com")
    report = await _underived(http_client, h1)
    first = await _post(http_client, h1, _set(rpe=9.0, supersedes_report_id=report["id"]))
    assert first.status_code == 200
    resp = await _post(http_client, h2, _set(rpe=9.0, supersedes_report_id=report["id"]))
    assert resp.status_code == 404, resp.text
    # Not a 409 naming the superseder: nothing about another athlete's report is revealed.
    assert resp.json() == {"detail": f"no strength report {report['id']}"}
    assert (await http_client.get(f"/v1/benchmarks/strength-reports/{report['id']}", headers=h2)).status_code == 404
    assert await _committed(async_db, _count(BenchmarkObservation, u2)) == 0


async def test_a_report_is_superseded_only_by_the_same_lift(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "s-lift@test.com")
    report = await _underived(http_client, headers)
    resp = await _post(http_client, headers, _set(BENCH, rpe=9.0, supersedes_report_id=report["id"]))
    assert resp.status_code == 400, resp.text
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0


@pytest.mark.parametrize("other", [
    {"load_kg": 102.5},
    {"reps": 4},
    {"performed_at": "days:2"},
    {"method": "tested_max", "value_kg": 120.0, "load_kg": None, "reps": None, "rpe": None},
], ids=["load", "reps", "performed_at", "tested_max"])
async def test_only_the_same_performance_supersedes_a_report(http_client, async_db, other):
    """A different performance is its own recording, never a correction of the report."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, f"s-facts-{len(str(other))}@test.com")
    report = await _underived(http_client, headers)
    body = _set(rpe=9.0, supersedes_report_id=report["id"])
    body.update({k: (_ago(2) if v == "days:2" else v) for k, v in other.items()})
    body = {k: v for k, v in body.items() if v is not None}
    resp = await _post(http_client, headers, body)
    assert resp.status_code == 400, resp.text
    assert "same load, reps and performance time" in resp.json()["detail"]
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0
    assert await _committed(async_db, select(UnderivedStrengthReport.superseded_by_observation_id).where(
        UnderivedStrengthReport.id == report["id"]
    )) is None
    # Without the claim it is simply recorded, and the report still stands.
    body.pop("supersedes_report_id")
    assert (await _post(http_client, headers, body)).json()["outcome"] == "recorded"
    listed = (await http_client.get("/v1/benchmarks/strength-reports", headers=headers)).json()
    assert [r["id"] for r in listed] == [report["id"]]


async def test_a_supersession_race_on_two_connections_supersedes_once(engine):
    """Two different corrections of one report, on two connections at once: one supersedes it,
    the other is a 409 naming the winner's observation, and only the winner's key is stored."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        await _catalog(db)
        user = User(email="s-race@test.com", hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        user_id = user.id
        staged = await ses.record_strength_evidence(
            db, user_id, StrengthEvidenceCreate.model_validate(_set())
        )
        assert staged.report is not None
        report_id = staged.report.id

    async def correct(rpe: float) -> Any:
        body = StrengthEvidenceCreate.model_validate(
            _set(rpe=rpe, supersedes_report_id=report_id, idempotency_key=str(uuid.uuid4()))
        )
        async with factory() as db:
            try:
                return await ses.record_strength_evidence(db, user_id, body)
            except ses.ReportAlreadySuperseded as e:
                return e

    results = await asyncio.gather(correct(9.0), correct(9.5))
    [won] = [r for r in results if not isinstance(r, Exception)]
    [lost] = [r for r in results if isinstance(r, ses.ReportAlreadySuperseded)]
    assert won.outcome == "recorded" and won.observation is not None
    assert lost.superseded_by_observation_id == won.observation.id
    async with factory() as db:
        assert await db.scalar(_count(BenchmarkObservation, user_id)) == 1
        assert await db.scalar(_count(StrengthSubmissionKey, user_id)) == 1
        assert await db.scalar(select(UnderivedStrengthReport.superseded_by_observation_id).where(
            UnderivedStrengthReport.id == report_id
        )) == won.observation.id


async def test_a_failure_after_the_supersession_link_flushes_undoes_both(http_client, async_db, monkeypatch):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "s-fail@test.com")
    report = await _underived(http_client, headers)

    async def fails(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected after the link flushed")

    monkeypatch.setattr(ses, "_stage_projection", fails)
    with pytest.raises(RuntimeError, match="injected"):
        await _post(http_client, headers, _set(rpe=9.0, supersedes_report_id=report["id"]))
    await async_db.rollback()
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0
    assert await _committed(async_db, select(UnderivedStrengthReport.superseded_by_observation_id).where(
        UnderivedStrengthReport.id == report["id"]
    )) is None


# ── read path ────────────────────────────────────────────────────────────────────


async def test_the_athlete_reads_only_their_reports(http_client, async_db):
    await _catalog(async_db)
    h1, _ = await _athlete(http_client, async_db, "r-mine@test.com")
    h2, _ = await _athlete(http_client, async_db, "r-theirs@test.com")
    older = await _underived(http_client, h1)
    newer = (await _post(http_client, h1, _set(BENCH, performed_at=_ago(0.5)))).json()["report"]
    await _underived(http_client, h2)
    listed = (await http_client.get("/v1/benchmarks/strength-reports", headers=h1)).json()
    assert [r["id"] for r in listed] == [newer["id"], older["id"]]
    only_squat = (await http_client.get(
        "/v1/benchmarks/strength-reports", params={"benchmark_code": SQUAT}, headers=h1
    )).json()
    assert [r["id"] for r in only_squat] == [older["id"]]


async def test_the_list_shows_active_reports_and_history_stays_readable_by_id(http_client, async_db):
    """The superseded report is filtered before the limit, so it never takes an active one's
    place; its permanent record is still at its id."""
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "r-active@test.com")
    older = (await _post(http_client, headers, _set(performed_at=_ago(3)))).json()["report"]
    newer = (await _post(http_client, headers, _set(performed_at=_ago(1)))).json()["report"]
    fix = await _post(http_client, headers, _set(rpe=9.0, performed_at=_ago(1), supersedes_report_id=newer["id"]))
    assert fix.json()["outcome"] == "recorded", fix.text
    limited = (await http_client.get(
        "/v1/benchmarks/strength-reports", params={"limit": 1}, headers=headers
    )).json()
    assert [r["id"] for r in limited] == [older["id"]]
    shown = (await http_client.get(f"/v1/benchmarks/strength-reports/{newer['id']}", headers=headers)).json()
    assert shown["superseded_by_observation_id"] == fix.json()["observation"]["id"]
