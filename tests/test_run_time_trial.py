"""B2 — the 1.5 mi time trial through the real routes (live DB).

What this pins, through ``POST /v1/onboard``, ``POST /v1/benchmarks/observations``,
``GET /v1/benchmarks/vo2-estimates``, ``PATCH /v1/profile`` and ``/v1/objectives``:

1. Onboarding saves the run as a ``run_1p5mi_time_trial`` observation with its captured inputs
   and a display-only estimate; missing estimate inputs never refuse the run or onboarding.
2. A saved estimate is rebuilt from its own captured inputs: later profile edits do not move it.
3. The record has no authority: it changes no state row or capacity value, opens no weak point,
   writes no floor shadow, never sizes a load, and never counts as an objective's attainment —
   even after the catalog row is made hostile (a mapping, scoring rules and weight added) and
   the writer asks for everything. Positive controls show the same harness does see a change.
4. The old ``run_vo2_field_test_300m_1p5mi`` definition and its observations are untouched.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.logic import vo2_estimate as vo2
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.capacity_floor_shadow import CapacityFloorShadowLog
from app.models.observation_mapping import ObservationMapping
from app.models.weak_point import WeakPoint
from app.repositories.benchmark_observation_repository import select_prescription_basis
from app.scripts.seed_benchmarks import BENCHMARKS, MAPPINGS
from app.services import state_service

pytestmark = pytest.mark.asyncio

OLD = "run_vo2_field_test_300m_1p5mi"
NEW = vo2.PROTOCOL_CODE
DOB = "2000-05-01"
RAN_ON = "2026-10-01"  # age 26 at performance: within the study's age range
MALE_163LB_1200 = 46.288271875064  # worked by hand in tests/test_vo2_estimate.py


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", True)


async def _seed_catalog(db: AsyncSession) -> None:
    """The two definitions exactly as the real catalog seeds them, and the old one's mapping."""
    by_code = {row["code"]: row for row in BENCHMARKS}
    defs = {code: BenchmarkDefinition(**by_code[code]) for code in (OLD, NEW)}
    db.add_all(defs.values())
    await db.flush()
    for m in MAPPINGS:
        if m["benchmark_code"] in defs:
            db.add(ObservationMapping(
                benchmark_definition_id=defs[m["benchmark_code"]].id,
                target_vector=m["target_vector"], target_key=m["target_key"],
                mapping_type=m["mapping_type"], coefficient=m["coefficient"],
                intercept=m.get("intercept", 0.0), config=m.get("config"),
            ))
    await db.commit()


async def _token(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _payload(run: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "goal": "Running",
        "date_of_birth": DOB,
        "equipment": ["bodyweight"],
        "available_days_per_week": 4,
        "session_duration_minutes": 45,
    }
    if run is not None:
        body["run_time_trial"] = run
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


FULL_RUN = {"elapsed_seconds": 720, "performed_on": RAN_ON, "body_mass": 163,
            "body_mass_unit": "lb", "sex_for_estimate": "male"}


async def _onboard(client: AsyncClient, email: str, body: dict[str, Any]) -> tuple[dict, dict]:
    headers = await _token(client, email)
    resp = await client.post("/v1/onboard", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return headers, resp.json()


async def _rows(db: AsyncSession, user_id: int, code: str) -> list[BenchmarkObservation]:
    return list((await db.execute(
        select(BenchmarkObservation)
        .join(BenchmarkDefinition)
        .where(BenchmarkObservation.user_id == user_id, BenchmarkDefinition.code == code)
        .order_by(BenchmarkObservation.id)
        .execution_options(populate_existing=True)
    )).scalars().all())


async def _count(db: AsyncSession, model: Any, user_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(model).where(model.user_id == user_id)
    )).scalar_one()


async def _capacity(db: AsyncSession, user_id: int) -> dict[str, float]:
    state = await state_service.load_current_state_strict(db, user_id)
    assert state is not None
    return {k: float(getattr(state.capacity_x, k)) for k in CapacityState.KEYS}


def _columns(row: Any) -> dict[str, Any]:
    return {attr.key: getattr(row, attr.key) for attr in inspect(row).mapper.column_attrs}


# --- 1. onboarding saves the run, with a display-only estimate ------------------------------------


@pytest.mark.usefixtures("verified")
async def test_onboarding_saves_the_run_with_its_inputs_and_a_display_only_estimate(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, body = await _onboard(http_client, "tt_full@test.com", _payload(FULL_RUN))

    est = body["run_time_trial"]
    assert est["status"] == "estimated"
    assert est["vo2max_ml_kg_min"] == pytest.approx(MALE_163LB_1200, abs=1e-9)
    assert est["units"] == "ml/kg/min"
    assert est["headline"] == "Estimated VO₂max — not used for your plan."
    assert est["used_for_plan"] is False
    assert (est["population_status"], est["extrapolated"]) == ("within_study_age_range", False)
    assert est["population_label"] == "Within the study's age range (18–29)"
    assert est["see_ml_kg_min"] == 3.9
    assert (est["model_id"], est["model_version"]) == ("george1993_1p5mi_run", 1)
    assert est["performed_on"] == RAN_ON
    assert est["elapsed_seconds"] == 720

    [row] = await _rows(async_db, body["user_id"], NEW)
    assert row.raw_value == 720  # the performance itself, as reported
    assert (row.evidence_type, row.value_semantics) == (
        se.EV_DISPLAY_ONLY_PERFORMANCE, se.VS_MEASURED
    )
    assert (row.capacity_effect, row.affects_capacity, row.affects_prescription) == (
        oa.CE_NONE, False, False
    )
    assert row.provenance_operation == oa.OP_RUN_TIME_TRIAL
    assert row.collection_mode == oa.CM_ONBOARDING_ONRAMP
    assert (row.protocol_code, row.protocol_version) == (NEW, "1")
    assert (row.observation_model, row.model_version) == ("george1993_1p5mi_run", "1")
    assert row.normalized_value is None and row.state_disposition is None
    assert row.performed_at is not None and row.performed_at.date() in (
        date(2026, 9, 30), date(2026, 10, 1)  # local midnight in the athlete's zone, as UTC
    )
    meta = row.protocol_metadata
    assert meta["inputs"]["body_mass_kg"] == pytest.approx(163 * 0.45359237, abs=1e-12)
    assert meta["inputs"]["body_mass_entered"] == {"value": 163.0, "unit": "lb"}
    assert meta["inputs"]["sex_model_input"] == 1
    assert meta["inputs"]["age_at_performance_years"] == 26
    assert meta["outcome_at_write"]["vo2max"] == pytest.approx(MALE_163LB_1200, abs=1e-9)
    assert meta["uncertainty"]["see"] == 3.9

    # Kept in history, readable through the generic list as well.
    listed = await http_client.get(
        f"/v1/benchmarks/observations?benchmark_code={NEW}", headers=headers
    )
    assert listed.status_code == 200
    assert [(o["id"], o["raw_value"]) for o in listed.json()] == [(row.id, 720.0)]


@pytest.mark.usefixtures("verified")
async def test_an_athlete_outside_the_age_range_is_labelled_extrapolated(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    _, body = await _onboard(
        http_client, "tt_older@test.com", _payload(FULL_RUN, date_of_birth="1985-03-02")
    )
    est = body["run_time_trial"]
    assert est["status"] == "estimated"
    assert (est["population_status"], est["extrapolated"]) == ("outside_study_age_range", True)
    assert est["population_label"].startswith("Extrapolated and unvalidated")


# --- 2. a saved estimate reproduces from its captured inputs --------------------------------------


@pytest.mark.usefixtures("verified")
async def test_a_saved_estimate_reproduces_after_the_profile_changes(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, body = await _onboard(
        http_client, "tt_reproduce@test.com", _payload(FULL_RUN, bodyweight_kg=74.0)
    )
    saved = body["run_time_trial"]

    patched = await http_client.patch(
        "/v1/profile", json={"bodyweight_kg": 101.5, "date_of_birth": "1970-01-01"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert (profile["bodyweight_kg"], profile["date_of_birth"]) == (101.5, "1970-01-01")

    listed = await http_client.get("/v1/benchmarks/vo2-estimates", headers=headers)
    assert listed.status_code == 200, listed.text
    [now] = listed.json()
    assert now["vo2max_ml_kg_min"] == pytest.approx(saved["vo2max_ml_kg_min"], abs=1e-12)
    assert now["vo2max_ml_kg_min"] == pytest.approx(MALE_163LB_1200, abs=1e-9)
    assert now["population_status"] == saved["population_status"] == "within_study_age_range"
    assert now == saved


# --- 3. missing inputs save the run; onboarding completes ----------------------------------------


@pytest.mark.parametrize("gate_open", [True, False])
async def test_missing_inputs_save_the_run_and_onboarding_completes(
    http_client: AsyncClient, async_db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    gate_open: bool,
) -> None:
    monkeypatch.setattr(vo2, "GEORGE_1993_COEFFICIENTS_VERIFIED", gate_open)
    await _seed_catalog(async_db)
    # No date of birth, no body mass, no sex input, no run date: only the time.
    headers, body = await _onboard(
        http_client, f"tt_missing_{gate_open}@test.com",
        _payload({"elapsed_seconds": 744}, date_of_birth=None),
    )
    est = body["run_time_trial"]
    assert est["status"] == "not_estimated" and est["vo2max_ml_kg_min"] is None
    assert est["reason"] == ("missing_inputs" if gate_open else "coefficients_unverified")
    assert est["missing_inputs"] == ["body_mass", "sex"]
    assert (est["population_status"], est["extrapolated"]) == ("age_unknown", True)
    assert est["elapsed_seconds"] == 744

    [row] = await _rows(async_db, body["user_id"], NEW)
    assert row.raw_value == 744 and row.performed_at is None

    done = await http_client.post(
        "/v1/onboarding/complete", json={"reason": "finished"}, headers=headers
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"


@pytest.mark.usefixtures("verified")
async def test_a_retried_submission_records_the_run_once(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, first = await _onboard(http_client, "tt_retry@test.com", _payload(FULL_RUN))
    again = await http_client.post("/v1/onboard", json=_payload(FULL_RUN), headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["run_time_trial"] == first["run_time_trial"]
    assert len(await _rows(async_db, first["user_id"], NEW)) == 1


@pytest.mark.parametrize(
    ("performed_on", "message"),
    [
        ((datetime.now(UTC) + timedelta(days=3)).date().isoformat(), "in the future"),
        ("1999-12-31", "before your date of birth"),
    ],
)
async def test_an_impossible_run_date_refuses_only_the_run(
    http_client: AsyncClient, async_db: AsyncSession, performed_on: str, message: str,
) -> None:
    await _seed_catalog(async_db)
    headers = await _token(http_client, f"tt_bad_{performed_on}@test.com")
    resp = await http_client.post(
        "/v1/onboard", json=_payload({**FULL_RUN, "performed_on": performed_on}), headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_time_trial"] is None
    assert body["run_time_trial_refused"].startswith("The 1.5 mi run was not saved")
    assert message in body["run_time_trial_refused"]
    total = (await async_db.execute(select(func.count()).select_from(BenchmarkObservation)))
    assert total.scalar_one() == 0
    # Everything else in the submission was saved, and onboarding goes on.
    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert (profile["date_of_birth"], profile["equipment"]) == (DOB, ["bodyweight"])
    assert (await http_client.get("/v1/onboarding/state", headers=headers)).json()[
        "can_prescribe"
    ] is True


def test_an_unknown_zone_allows_the_latest_date_anywhere() -> None:
    from app.logic.athlete_calendar import UNKNOWN, AthleteCalendar
    from app.services.run_time_trial_service import latest_possible_today

    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # already 22 Oct at UTC+14
    assert latest_possible_today(UNKNOWN, now) == date(2026, 10, 22)
    assert latest_possible_today(UNKNOWN, datetime(2026, 10, 21, 9, 59, tzinfo=UTC)) == date(
        2026, 10, 21
    )
    # A known zone is held to its own today: in Honolulu (UTC-10) it is still 21 Oct.
    assert latest_possible_today(AthleteCalendar("Pacific/Honolulu"), now) == date(2026, 10, 21)
    assert latest_possible_today(AthleteCalendar("Pacific/Kiritimati"), now) == date(2026, 10, 22)


@pytest.mark.parametrize(
    ("mass", "unit", "ok"),
    [(25, "kg", True), (300, "kg", True), (24.9, "kg", False), (301, "kg", False),
     (55.2, "lb", True), (661, "lb", True), (55, "lb", False), (700, "lb", False)],
)
def test_body_mass_bounds_are_in_kg_whatever_the_unit(mass: float, unit: str, ok: bool) -> None:
    from pydantic import ValidationError

    from app.schemas.benchmarks import RunTimeTrialReport

    body = {"elapsed_seconds": 720, "body_mass": mass, "body_mass_unit": unit}
    if ok:
        RunTimeTrialReport.model_validate(body)
        return
    with pytest.raises(ValidationError, match="body_mass must be between 25 and 300 kg"):
        RunTimeTrialReport.model_validate(body)


async def test_an_implausible_body_mass_is_a_422_that_names_the_field(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers = await _token(http_client, "tt_mass@test.com")
    resp = await http_client.post(
        "/v1/onboard", json=_payload({**FULL_RUN, "body_mass": 1630}), headers=headers
    )
    assert resp.status_code == 422
    assert "body_mass must be between 25 and 300 kg (55.2-661.3 lb)" in resp.text
    assert "less than or equal to" not in resp.text


async def test_a_missing_definition_refuses_the_run_and_saves_onboarding(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    # The boot seed failed before the new definition was inserted (Dockerfile tolerates it).
    async_db.add(BenchmarkDefinition(**{row["code"]: row for row in BENCHMARKS}[OLD]))
    await async_db.commit()
    headers, body = await _onboard(http_client, "tt_nodef@test.com", _payload(FULL_RUN))
    assert body["run_time_trial"] is None
    assert body["run_time_trial_refused"] == (
        "The 1.5 mi run was not saved: time trials are unavailable right now."
    )
    assert (await http_client.get("/v1/profile", headers=headers)).json()["date_of_birth"] == DOB


@pytest.mark.usefixtures("verified")
async def test_estimates_are_listed_by_when_the_run_happened(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, _ = await _onboard(
        http_client, "tt_order@test.com", _payload({**FULL_RUN, "performed_on": "2026-09-20"})
    )
    # Submitted later, but run earlier; then one with no date at all.
    for run in ({**FULL_RUN, "performed_on": "2026-08-01"}, {"elapsed_seconds": 800}):
        resp = await http_client.post("/v1/onboard", json=_payload(run), headers=headers)
        assert resp.status_code == 200, resp.text
    listed = (await http_client.get("/v1/benchmarks/vo2-estimates", headers=headers)).json()
    assert [e["performed_on"] for e in listed] == ["2026-09-20", "2026-08-01", None]


# --- 4. no capacity, prescription or attainment authority ----------------------------------------


@pytest.mark.usefixtures("verified")
async def test_onboarding_with_a_run_seeds_the_same_state_as_without(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    _, without = await _onboard(http_client, "tt_ctrl@test.com", _payload())
    _, with_run = await _onboard(http_client, "tt_run@test.com", _payload(FULL_RUN))

    assert await _count(async_db, AthleteState, with_run["user_id"]) == await _count(
        async_db, AthleteState, without["user_id"]
    )
    assert await _capacity(async_db, with_run["user_id"]) == await _capacity(
        async_db, without["user_id"]
    )


@pytest.mark.usefixtures("verified")
async def test_a_hostile_writer_and_catalog_still_get_no_authority(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, body = await _onboard(http_client, "tt_hostile@test.com", _payload())
    uid = body["user_id"]

    # Make the catalog row as hostile as a bad edit could: an aerobic mapping, scoring rules that
    # put this time at the top of the scale, a target and a full weight.
    new_def = (await async_db.execute(
        select(BenchmarkDefinition).where(BenchmarkDefinition.code == NEW)
    )).scalar_one()
    new_def.standardization_rules = {"floor": 900.0, "cap": 400.0}
    new_def.state_targets = ["aerobic"]
    new_def.observation_weight = 1.0
    async_db.add(ObservationMapping(
        benchmark_definition_id=new_def.id, target_vector="capacity", target_key="aerobic",
        mapping_type="residual", coefficient=1.0, intercept=0.0, config={},
    ))
    await async_db.commit()

    states_before = await _count(async_db, AthleteState, uid)
    capacity_before = await _capacity(async_db, uid)

    objective = await http_client.post(
        "/v1/objectives",
        json={"label": "1.5 mi", "benchmark_code": NEW, "target_value": 600.0},
        headers=headers,
    )
    assert objective.status_code == 200, objective.text

    hostile = await http_client.post("/v1/benchmarks/observations", headers=headers, json={
        "benchmark_code": NEW, "raw_value": 500.0, "source": "manual",
        "evidence_type": se.EV_DIRECT_MEASUREMENT, "value_semantics": se.VS_MEASURED,
        "requested_capacity_effect": oa.CE_BIDIRECTIONAL_UPDATE, "affects_prescription": True,
        "normalized_value": 10.0, "observation_model": "benchmark_protocol",
        "protocol_metadata": {"outcome_at_write": {"status": "estimated", "vo2max": 99.0}},
    })
    assert hostile.status_code == 200, hostile.text

    # No state: no new row, no capacity change, no weak point, no floor shadow.
    assert await _count(async_db, AthleteState, uid) == states_before
    assert await _capacity(async_db, uid) == capacity_before
    assert await _count(async_db, WeakPoint, uid) == 0
    assert await _count(async_db, CapacityFloorShadowLog, uid) == 0

    [row] = await _rows(async_db, uid, NEW)
    assert (row.evidence_type, row.capacity_effect, row.affects_prescription) == (
        se.EV_DISPLAY_ONLY_PERFORMANCE, oa.CE_NONE, False
    )
    # The writer's request is replaced by the server's, not merely outvoted by the policy.
    assert row.requested_capacity_effect == oa.CE_NONE
    assert row.observation_model == "george1993_1p5mi_run"
    assert row.normalized_value is None
    # The client's metadata is discarded: no forged estimate is stored or shown.
    assert row.protocol_metadata["outcome_at_write"]["status"] == "not_estimated"
    shown = (await http_client.get("/v1/benchmarks/vo2-estimates", headers=headers)).json()
    assert [e["vo2max_ml_kg_min"] for e in shown] == [None]

    # Never a prescription basis.
    basis = await select_prescription_basis(async_db, uid, {NEW}, as_of=datetime.now(UTC))
    assert basis[NEW].selected is None

    # Never an objective's attainment.
    objectives = (await http_client.get("/v1/objectives", headers=headers)).json()
    [progress] = [o["progress"] for o in objectives if o["benchmark_code"] == NEW]
    assert progress["current"] is None and progress["pct"] is None

    # Positive control: the same harness sees a real change. The old definition's observation,
    # posted the same way, writes a state row and becomes the objective's current value.
    control_objective = await http_client.post(
        "/v1/objectives", json={"label": "VO2", "benchmark_code": OLD, "target_value": 60.0},
        headers=headers,
    )
    assert control_objective.status_code == 200
    control = await http_client.post("/v1/benchmarks/observations", headers=headers, json={
        "benchmark_code": OLD, "raw_value": 62.0, "source": "manual",
    })
    assert control.status_code == 200, control.text
    assert await _count(async_db, AthleteState, uid) == states_before + 1
    assert await _capacity(async_db, uid) != capacity_before
    objectives = (await http_client.get("/v1/objectives", headers=headers)).json()
    [control_progress] = [o["progress"] for o in objectives if o["benchmark_code"] == OLD]
    assert control_progress["current"] == 62.0


# --- 5. history is untouched -----------------------------------------------------------------------


@pytest.mark.usefixtures("verified")
async def test_the_old_definition_and_its_observations_are_untouched(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    headers, body = await _onboard(http_client, "tt_history@test.com", _payload())
    uid = body["user_id"]
    old_def = (await async_db.execute(
        select(BenchmarkDefinition).where(BenchmarkDefinition.code == OLD)
    )).scalar_one()
    # A historical row the way legacy history looks after migration a028.
    async_db.add(BenchmarkObservation(
        user_id=uid, benchmark_definition_id=old_def.id, raw_value=44.3,
        observed_at=datetime(2026, 3, 1, 12, 0), validity_status="valid", source="manual",
        protocol_metadata={"time_300m": "0:52", "time_1p5mi": "9:18"},
        evidence_type=se.EV_DIRECT_MEASUREMENT, value_semantics=se.VS_MEASURED,
        source_type=oa.ST_LEGACY_UNKNOWN, provenance_operation=oa.OP_SCHEMA_BACKFILL,
        migration_version=oa.LEGACY_MIGRATION_VERSION, capacity_effect=oa.CE_NONE,
    ))
    await async_db.commit()
    [old_row] = await _rows(async_db, uid, OLD)
    def_before, row_before = _columns(old_def), _columns(old_row)

    resubmitted = await http_client.post("/v1/onboard", json=_payload(FULL_RUN), headers=headers)
    assert resubmitted.status_code == 200, resubmitted.text
    posted = await http_client.post("/v1/benchmarks/observations", headers=headers, json={
        "benchmark_code": NEW, "raw_value": 700.0, "source": "manual",
    })
    assert posted.status_code == 200, posted.text
    assert (await http_client.get("/v1/benchmarks/vo2-estimates", headers=headers)).status_code == 200

    [old_after] = await _rows(async_db, uid, OLD)
    def_after = (await async_db.execute(
        select(BenchmarkDefinition).where(BenchmarkDefinition.code == OLD)
        .execution_options(populate_existing=True)
    )).scalar_one()
    assert _columns(old_after) == row_before
    assert _columns(def_after) == def_before
    # The new runs are only ever new rows: the time trials read none of the old history.
    assert len(await _rows(async_db, uid, NEW)) == 2
