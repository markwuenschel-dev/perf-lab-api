"""P4-3b: an underived strength report explains a missing weight (``effort_unknown`` /
``effort_conflict``), under one rule:

* a basis always wins — a report never explains a lift that has a weight;
* only a report performed inside the window a basis would need counts (dated, not in the future,
  within the freshness horizon), and the most recently performed one;
* it replaces ``no_evidence`` and ``stale``;
* it replaces ``set_not_qualifying`` only when it is newer than that set and its reps are within
  the qualifying gate; every other category stands;
* a superseded report explains nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.logic import observation_authority as oa
from app.logic import prescription_evidence as pe
from app.logic import strength_evidence as se
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.strength_report import UnderivedStrengthReport
from app.models.user import User
from app.schemas.prescription import ExercisePrescription, LoadExplanation, WorkoutPrescription
from app.services.prescription_service import _enrich_exercises_with_load

SQUAT = "pl_e1rm_squat"
AS_OF = datetime(2026, 10, 12, 12, 0, 0)
_BLOCK = {"week_number": 1, "duration_weeks": 4}
_HORIZON = timedelta(days=pe.STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS)


def _report(days_ago: float | None, *, reps: int = 5, reason: str = "effort_unknown",
            report_id: int = 1) -> pe.UnderivedReportRow:
    performed = None if days_ago is None else AS_OF - timedelta(days=days_ago)
    return pe.UnderivedReportRow(report_id=report_id, performed_at=performed, reps=reps, reason=reason)


def _row(**fields: Any) -> pe.EvidenceRow:
    base: dict[str, Any] = {
        "observation_id": 1, "raw_value": 140.0, "performed_at": AS_OF - timedelta(days=2),
        "validity_status": "valid", "quarantined_at": None, "affects_prescription": True,
        "source_type": oa.ST_ATHLETE_ENTRY, "value_semantics": se.VS_MEASURED,
        "evidence_type": se.EV_DIRECT_MEASUREMENT,
    }
    base.update(fields)
    return pe.EvidenceRow(**base)


def _set_row(days_ago: float, reps: int = 8) -> pe.EvidenceRow:
    return _row(
        raw_value=100.0, performed_at=AS_OF - timedelta(days=days_ago), reps=reps, load_kg=80.0,
        rpe=9.0, effort_fidelity=se.FIDELITY_SET_LEVEL, value_semantics=se.VS_ESTIMATED,
        evidence_type=se.EV_ESTIMATED_FROM_TRAINING_SET,
    )


def _explains(rows: list[pe.EvidenceRow], reports: list[pe.UnderivedReportRow]) -> str | None:
    selection = pe.select_basis(rows, as_of=AS_OF)
    report = pe.explaining_report(selection, reports, as_of=AS_OF)
    return report.reason if report is not None else pe.explain_missing_basis(selection)


def test_a_basis_always_wins() -> None:
    assert _explains([_row()], [_report(1)]) is None


@pytest.mark.parametrize("reason", ["effort_unknown", "effort_conflict"])
def test_a_recent_report_replaces_no_evidence(reason: str) -> None:
    assert _explains([], [_report(1, reason=reason)]) == reason


def test_a_recent_report_replaces_stale() -> None:
    stale = _row(performed_at=AS_OF - _HORIZON - timedelta(days=1))
    assert pe.explain_missing_basis(pe.select_basis([stale], as_of=AS_OF)) == pe.EXPLAIN_STALE
    assert _explains([stale], [_report(1)]) == "effort_unknown"


@pytest.mark.parametrize(("days_ago", "counts"), [
    pytest.param(0, True, id="today"),
    pytest.param(_HORIZON.days, True, id="at-the-horizon"),
    pytest.param(_HORIZON.days + 0.01, False, id="past-the-horizon"),
    pytest.param(-0.01, False, id="future"),
    pytest.param(None, False, id="undated"),
])
def test_only_a_report_inside_the_basis_window_counts(days_ago: float | None, counts: bool) -> None:
    assert _explains([], [_report(days_ago)]) == ("effort_unknown" if counts else pe.EXPLAIN_NO_EVIDENCE)


def test_the_most_recently_performed_report_explains() -> None:
    reports = [_report(1, reason="effort_conflict", report_id=1), _report(3, report_id=2)]
    assert _explains([], reports) == "effort_conflict"


@pytest.mark.parametrize(("report_days_ago", "report_reps", "explained"), [
    pytest.param(1, 5, "effort_unknown", id="newer-within-gate"),
    pytest.param(3, 5, pe.EXPLAIN_SET_NOT_QUALIFYING, id="older"),
    pytest.param(2, 5, pe.EXPLAIN_SET_NOT_QUALIFYING, id="same-instant-is-not-newer"),
    pytest.param(1, 6, pe.EXPLAIN_SET_NOT_QUALIFYING, id="reps-past-the-gate"),
])
def test_a_report_replaces_set_not_qualifying_only_when_newer_and_within_the_gate(
    report_days_ago: float, report_reps: int, explained: str
) -> None:
    rows = [_set_row(days_ago=2)]
    assert pe.explain_missing_basis(pe.select_basis(rows, as_of=AS_OF)) == pe.EXPLAIN_SET_NOT_QUALIFYING
    assert _explains(rows, [_report(report_days_ago, reps=report_reps)]) == explained


@pytest.mark.parametrize(("row", "category"), [
    pytest.param(_row(performed_at=None), pe.EXPLAIN_MISSING_DATE, id="missing_performance_date"),
    pytest.param(_row(affects_prescription=False, evidence_type=se.EV_REPORTED_ESTIMATE,
                      value_semantics=se.VS_ESTIMATED), pe.EXPLAIN_ESTIMATE_NOT_USED, id="estimate_not_used"),
    pytest.param(_row(source_type="legacy_unknown"), pe.EXPLAIN_NOT_QUALIFYING, id="not_qualifying"),
])
def test_every_other_category_stands(row: pe.EvidenceRow, category: str) -> None:
    assert _explains([row], [_report(1)]) == category


# ── through the real query and the enrichment ────────────────────────────────────


async def _athlete(db: Any, email: str) -> tuple[int, int]:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    db.add(Exercise(name="Back Squat", modality="Strength", movement_pattern="squat",
                    load_type="barbell", is_benchmark=True, e1rm_benchmark_code=SQUAT))
    definition = BenchmarkDefinition(
        code=SQUAT, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.commit()
    return user.id, definition.id


def _rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="strength", focus="lower", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE")],
    )


async def _underived(db: Any, user_id: int, definition_id: int, *, days_ago: float,
                     superseded_by: int | None = None) -> None:
    db.add(UnderivedStrengthReport(
        user_id=user_id, benchmark_definition_id=definition_id, method="rep_set",
        collection_mode="retest", performed_at=AS_OF - timedelta(days=days_ago), load_kg=100.0,
        reps=5, reason="effort_unknown", superseded_by_observation_id=superseded_by,
        superseded_at=AS_OF if superseded_by is not None else None,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_the_prescribed_lift_says_effort_unknown(async_db):
    user_id, definition_id = await _athlete(async_db, "x-unknown@test.com")
    await _underived(async_db, user_id, definition_id, days_ago=1)
    rx = _rx()
    await _enrich_exercises_with_load(async_db, user_id, rx, _BLOCK, as_of=AS_OF)
    assert rx.exercises[0].prescribed_load_kg is None
    assert rx.exercises[0].load_explanation == LoadExplanation(
        status="no_qualifying_evidence", reason="effort_unknown", benchmark_code=SQUAT,
        evaluated_at=AS_OF.replace(tzinfo=UTC),
        evidence_performed_at=(AS_OF - timedelta(days=1)).replace(tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_a_superseded_report_explains_nothing(async_db):
    user_id, definition_id = await _athlete(async_db, "x-superseded@test.com")
    obs = BenchmarkObservation(
        user_id=user_id, benchmark_definition_id=definition_id, observed_at=AS_OF,
        raw_value=140.0, validity_status="valid", source="manual", affects_prescription=False,
    )
    async_db.add(obs)
    await async_db.commit()
    await _underived(async_db, user_id, definition_id, days_ago=1, superseded_by=obs.id)
    rx = _rx()
    await _enrich_exercises_with_load(async_db, user_id, rx, _BLOCK, as_of=AS_OF)
    explanation = rx.exercises[0].load_explanation
    assert explanation is not None and explanation.reason not in ("effort_unknown", "effort_conflict")
