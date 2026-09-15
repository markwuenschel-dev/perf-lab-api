"""Assess strength evidence (S2 decision 2, E1): characterized, evidence-only, server-derived.

An athlete reports a tested max, a set, or an estimate for a canonical lift without logging
a workout. The route states what happened; the server derives the value semantics and the
estimate, the SAME qualifying-set gate workout extraction faces decides whether a reported
set may size a load, no workout or dose is created, and the client cannot assert authority.
Driven through the real app, route to row to selection.
"""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.logic import strength_calibration as sc
from app.logic.prescription_evidence import (
    REASON_INSUFFICIENT_CHARACTERIZATION,
    REASON_MISSING_PERFORMED_AT,
)
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.repositories.benchmark_observation_repository import select_prescription_basis
from app.schemas.benchmarks import StrengthEvidenceCreate

pytestmark = pytest.mark.asyncio

_CODE = "pl_e1rm_squat"
_NOW = datetime.now(UTC).replace(microsecond=0)


async def _catalog(db) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code=_CODE,
    ))
    db.add(BenchmarkDefinition(
        code=_CODE, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    ))
    db.add(BenchmarkDefinition(
        code="sprint_300m_time", name="300 m time", domain="running", metric_type="time",
        unit="seconds", better_direction="lower", observation_weight=1.0,
        standardization_rules={"floor": 55.0, "cap": 32.0},
    ))
    await db.commit()


async def _athlete(client, db, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, user_id


def _days_ago(days: float) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


async def _report(client, headers, **body):
    payload = {"benchmark_code": _CODE, "collection_mode": "retest", **body}
    return await client.post("/v1/benchmarks/strength-evidence", json=payload, headers=headers)


async def _only_row(db, user_id: int) -> BenchmarkObservation:
    return (await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
    )).scalar_one()


async def _basis(db, user_id: int):
    return (await select_prescription_basis(db, user_id, {_CODE}, as_of=_NOW))[_CODE]


# ── what each method becomes ─────────────────────────────────────────────────────

async def test_a_dated_tested_max_is_measured_and_may_size_a_load(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-tested@test.com")

    resp = await _report(http_client, headers, method="tested_max", value_kg=140.0,
                         performed_at=_days_ago(2))
    assert resp.status_code == 200, resp.text

    row = await _only_row(async_db, user_id)
    assert (row.source_type, row.evidence_type, row.value_semantics) == (
        "athlete_entry", "direct_measurement", "measured"
    )
    assert row.raw_value == 140.0 and row.affects_prescription is True
    assert row.performed_at == (_NOW - timedelta(days=2)).replace(tzinfo=None)
    assert (await _basis(async_db, user_id)).selected is not None


async def test_the_server_estimates_a_reported_set_and_the_shared_gate_qualifies_it(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-set@test.com")

    resp = await _report(http_client, headers, method="rep_set", load_kg=120.0, reps=3, rpe=9.0,
                         performed_at=_days_ago(1))
    assert resp.status_code == 200, resp.text

    row = await _only_row(async_db, user_id)
    assert row.raw_value == sc.e1rm_from_set(120.0, 3)
    assert (row.evidence_type, row.value_semantics, row.formula) == (
        "estimated_from_training_set", "estimated", "epley"
    )
    assert (row.reps, row.load_kg, row.rpe, row.effort_fidelity) == (3, 120.0, 9.0, "set_level")
    assert (await _basis(async_db, user_id)).selected is not None


@pytest.mark.parametrize(
    "set_fields",
    [
        {"load_kg": 100.0, "reps": 8, "rpe": 9.0},   # past the 1-5 rep gate
        {"load_kg": 120.0, "reps": 3},              # no effort: nothing shows it was near failure
        {"load_kg": 120.0, "reps": 3, "rpe": 7.0},  # not near failure
    ],
)
async def test_a_reported_set_that_fails_the_gate_is_recorded_but_is_not_a_basis(
    http_client, async_db, set_fields
):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-set-fails@test.com")

    resp = await _report(http_client, headers, method="rep_set", performed_at=_days_ago(1), **set_fields)
    assert resp.status_code == 200, resp.text

    await _only_row(async_db, user_id)
    basis = await _basis(async_db, user_id)
    assert basis.selected is None
    assert basis.reason == REASON_INSUFFICIENT_CHARACTERIZATION


async def test_an_estimate_is_retained_as_reported_but_never_a_basis(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-estimate@test.com")

    resp = await _report(http_client, headers, method="estimate", value_kg=150.0,
                         performed_at=_days_ago(1))
    assert resp.status_code == 200, resp.text

    row = await _only_row(async_db, user_id)
    assert (row.evidence_type, row.value_semantics, row.affects_prescription) == (
        "reported_estimate", "estimated", False
    )
    assert (await _basis(async_db, user_id)).selected is None


async def test_an_undated_report_is_recorded_but_never_sizes_a_load(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-undated@test.com")

    resp = await _report(http_client, headers, method="tested_max", value_kg=140.0)
    assert resp.status_code == 200, resp.text

    assert (await _only_row(async_db, user_id)).performed_at is None
    basis = await _basis(async_db, user_id)
    assert basis.selected is None
    assert basis.reason == REASON_MISSING_PERFORMED_AT


# ── what the client may not do ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "smuggled",
    [
        {"value_semantics": "measured"},
        {"affects_prescription": True},
        {"raw_value": 999.0},
        {"evidence_type": "direct_measurement"},
        {"source": "benchmark_test"},
    ],
)
async def test_the_client_cannot_assert_authority_or_a_computed_value(http_client, async_db, smuggled):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "e1-smuggle@test.com")

    resp = await _report(http_client, headers, method="rep_set", load_kg=120.0, reps=3, rpe=9.0,
                         performed_at=_days_ago(1), **smuggled)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"method": "tested_max", "value_kg": 140.0, "reps": 1},      # a set field on a max
        {"method": "rep_set", "load_kg": 120.0},                     # a set without reps
        {"method": "rep_set", "reps": 3, "value_kg": 140.0},         # a set described by a weight
        {"method": "estimate"},                                      # no weight
        {"method": "tested_max", "value_kg": 0},
        {"method": "tested_max", "value_kg": -140.0},
        {"method": "rep_set", "load_kg": 120.0, "reps": 0},
        {"method": "rep_set", "load_kg": 120.0, "reps": 3, "rpe": 11},
    ],
)
async def test_inconsistent_or_impossible_values_are_refused(http_client, async_db, body):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "e1-invalid@test.com")
    assert (await _report(http_client, headers, **body)).status_code == 422


def test_a_non_finite_load_is_refused_at_the_schema() -> None:
    """JSON cannot carry Infinity or NaN; a direct caller of the schema is refused too."""
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            StrengthEvidenceCreate.model_validate(
                {"benchmark_code": _CODE, "method": "rep_set", "load_kg": bad, "reps": 3}
            )


async def test_a_future_performance_date_is_refused(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-future@test.com")

    resp = await _report(http_client, headers, method="tested_max", value_kg=140.0,
                         performed_at=(_NOW + timedelta(days=1)).isoformat())
    assert resp.status_code == 400
    rows = (await async_db.execute(
        select(func.count()).select_from(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
    )).scalar_one()
    assert rows == 0


@pytest.mark.parametrize("code", ["sprint_300m_time", "not_a_benchmark"])
async def test_only_a_canonical_lift_can_be_reported(http_client, async_db, code):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "e1-code@test.com")
    resp = await _report(http_client, headers, benchmark_code=code, method="tested_max", value_kg=140.0,
                         performed_at=_days_ago(1))
    assert resp.status_code == 400


# ── evidence only, and no stronger authority ─────────────────────────────────────

async def test_reporting_evidence_creates_no_workout(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-no-workout@test.com")

    for body in (
        {"method": "tested_max", "value_kg": 140.0, "performed_at": _days_ago(3)},
        {"method": "rep_set", "load_kg": 120.0, "reps": 3, "rpe": 9.0, "performed_at": _days_ago(2)},
        {"method": "estimate", "value_kg": 150.0},
    ):
        assert (await _report(http_client, headers, **body)).status_code == 200

    workouts = (await async_db.execute(
        select(func.count()).select_from(WorkoutLogORM).where(WorkoutLogORM.user_id == user_id)
    )).scalar_one()
    assert workouts == 0


async def test_capacity_authority_is_no_stronger_than_the_existing_policy(http_client, async_db):
    """A reported tested max resolves exactly as a plain manual entry of the same value
    already does; a reported set or an estimate never reaches bidirectional authority."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "e1-authority@test.com")

    plain = await http_client.post(
        "/v1/benchmarks/observations",
        json={"benchmark_code": _CODE, "raw_value": 140.0, "source": "manual",
              "validity_status": "valid", "collection_mode": "retest"},
        headers=headers,
    )
    assert plain.status_code == 200, plain.text
    for body in (
        {"method": "tested_max", "value_kg": 140.0, "performed_at": _days_ago(3)},
        {"method": "rep_set", "load_kg": 120.0, "reps": 3, "rpe": 9.0, "performed_at": _days_ago(2)},
        {"method": "estimate", "value_kg": 150.0},
    ):
        assert (await _report(http_client, headers, **body)).status_code == 200

    plain_row, tested, reported_set, estimate = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
        .order_by(BenchmarkObservation.id)
    )).scalars().all()
    assert tested.capacity_effect == plain_row.capacity_effect
    assert reported_set.capacity_effect != "bidirectional_update"
    assert estimate.capacity_effect != "bidirectional_update"


async def test_the_assessment_surface_marks_canonical_lift_cards(http_client, async_db):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "e1-surface@test.com")

    resp = await http_client.get("/v1/benchmarks/assessment-surface?mode=retest", headers=headers)
    assert resp.status_code == 200, resp.text
    cards = {c["code"]: c for g in resp.json()["groups"] for c in g["cards"]}
    assert cards[_CODE]["strength_evidence_entry"] is True
    assert cards["sprint_300m_time"]["strength_evidence_entry"] is False
