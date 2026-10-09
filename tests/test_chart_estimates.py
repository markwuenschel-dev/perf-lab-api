"""P4-2b: chart e1RM estimates for new evidence, behind ``E1RM_CHART_ESTIMATES`` (ADR-0056).

The stored e1RM of a set used to be Epley, whatever effort the athlete gave. With the flag on, a set
with a known, consistent effort inside the chart's domain is ``load / chart(reps, effort)``;
history is never rewritten. Pinned here:

* the estimate: an exact inverse of the prescription lookup, the same function for every route, no
  pre-log e1RM involved (so it is not the dose ladder inverted), and the legacy Epley for anything
  the chart cannot speak to or when the flag is off;
* **one number per set**: workout logging, Assess and the onboarding seed produce identical
  estimates for identical facts;
* **semantics and authority**: a chart row is a modeled estimate, PR or not, and carries no
  capacity authority: it records no floor candidate and writes no state row (ADR-0055);
* **retry identity**: a submission recorded under one formula is recognised under the other, adding
  no observation, no projection change and no state row;
* **coexistence**: the readers' policy with mixed formulas, stated as tests;
* the **activation report** shows each athlete's step before the flag is turned on, read-only.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.logic import observation_authority as oa
from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.capacity_floor_shadow import CapacityFloorShadowLog
from app.models.exercise import Exercise
from app.models.user import AthleteProfile, User
from app.repositories.benchmark_observation_repository import estimated_pr_baseline
from app.schemas.benchmarks import StrengthReport
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services import strength_evidence_service as ses
from app.services.e1rm_activation import activation_report
from app.services.prescription_service import _enrich_exercises_with_load
from app.services.state_service import process_new_workout

ROOT = Path(__file__).resolve().parents[1]
CODE = "pl_e1rm_squat"
T1 = (datetime.now(UTC) - timedelta(days=3)).replace(microsecond=0)


@pytest.fixture
def chart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", True)


async def _athlete(db, email: str) -> User:
    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    db.add(Exercise(name="Back Squat", modality="Strength", movement_pattern="squat",
                    load_type="barbell", is_benchmark=True, e1rm_benchmark_code=CODE))
    db.add(BenchmarkDefinition(
        code=CODE, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    ))
    await db.commit()
    await db.refresh(user)
    return user


def _session(at: datetime, *, load: float = 100.0, reps: int = 5, rpe: float | None = 8.5, rir: float | None = None):
    return WorkoutLog(
        timestamp=at, modality="Strength", duration_minutes=45.0, session_rpe=rpe or 7.0,
        sets=[WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=load, reps=reps, rpe=rpe, rir=rir)],
    )


async def _rows(db, user_id: int) -> list[BenchmarkObservation]:
    return list((await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
        .order_by(BenchmarkObservation.id)
    )).scalars())


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def _report(**kw) -> StrengthReport:
    base = {"benchmark_code": CODE, "method": "rep_set", "performed_at": T1 - timedelta(days=1),
            "load_kg": 100.0, "reps": 5, "rpe": 8.5}
    return StrengthReport(**{**base, **kw})


# ----- the estimate ---------------------------------------------------------------------------------- #

@pytest.mark.parametrize("reps", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("rpe", [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.49, 9.5, 10.0])
def test_the_chart_estimate_is_the_exact_inverse_of_the_prescription_lookup(reps, rpe):
    value = sc.chart_e1rm_unrounded(100.0, reps, rpe)
    assert abs(value * sc._chart_percent(reps, rpe) / 100.0 - 1.0) <= 1e-9  # before any rounding
    est = sc.estimate_e1rm(load_kg=100.0, reps=reps, rpe=rpe, rir=None, use_chart=True)
    assert (est.formula, est.model_version, est.modeled) == (sc.FORMULA_CHART, sc.MODEL_VERSION, True)
    assert est.value == round(value, 1)


def test_five_at_two_reps_in_reserve_is_five_at_rpe_eight_and_effort_is_continuous():
    by_rir = sc.estimate_e1rm(load_kg=120.0, reps=5, rpe=None, rir=2.0, use_chart=True)
    by_rpe = sc.estimate_e1rm(load_kg=120.0, reps=5, rpe=8.0, rir=None, use_chart=True)
    assert by_rir == by_rpe
    below = sc.chart_e1rm_unrounded(120.0, 3, 9.49)
    at = sc.chart_e1rm_unrounded(120.0, 3, 9.5)
    assert abs(at - below) / at < 0.002  # no jump across the failure threshold


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"rpe": None, "rir": None}, "effort unknown"),
        ({"rpe": 8.0, "rir": 0.0}, "RPE and RIR contradict each other"),
        ({"reps": 13, "rpe": 8.0, "rir": None}, "more reps than the chart covers"),
        ({"reps": 5, "rpe": 5.0, "rir": None}, "easier than the chart covers"),
    ],
)
def test_what_the_chart_cannot_speak_to_keeps_the_legacy_estimate(kw, why):
    args = {"load_kg": 100.0, "reps": 5, "rpe": 8.0, "rir": None, **kw}
    est = sc.estimate_e1rm(use_chart=True, **args)
    assert (est.formula, est.model_version) == (sc.FORMULA_EPLEY, None), why
    assert est.value == sc.e1rm_from_set(100.0, args["reps"])


def test_with_the_flag_off_every_set_is_the_legacy_estimate():
    est = sc.estimate_e1rm(load_kg=100.0, reps=5, rpe=8.5, rir=None, use_chart=False)
    assert (est.formula, est.value) == (sc.FORMULA_EPLEY, sc.e1rm_from_set(100.0, 5))


def test_the_estimate_never_reads_a_pre_log_e1rm_or_the_dose_ladder():
    """Inverting `external_intensity_for_set` when it picked `load / e1rm_pre` would hand the old
    estimate back. The estimate takes no denominator and does not call the ladder."""
    tree = ast.parse((ROOT / "app/logic/strength_calibration.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "estimate_e1rm")
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    called |= {c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert "external_intensity_for_set" not in called
    assert not {a.arg for a in fn.args.kwonlyargs + fn.args.args} & {"e1rm_pre", "e1rm_denominator"}


def test_chart_estimates_are_above_epley_for_the_sets_that_qualify():
    """The size of the step the activation report exists for: 0-12% above Epley for the same set."""
    for reps in range(1, 6):
        for rpe in (8.0, 8.5, 9.0, 9.5, 10.0):
            chart = sc.estimate_e1rm(load_kg=100.0, reps=reps, rpe=rpe, rir=None, use_chart=True).value
            epley = sc.e1rm_from_set(100.0, reps)
            assert 0.0 <= chart / epley - 1.0 <= 0.12, (reps, rpe)


# ----- one number per set ------------------------------------------------------------------------------ #

@pytest.mark.parametrize("flag", [False, True])
async def test_the_same_set_gives_the_same_estimate_through_workout_logging_and_assess(async_db, monkeypatch, flag):
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", flag)
    worker = await _athlete(async_db, f"par-w-{flag}@test.com")
    await process_new_workout(async_db, worker.id, _session(T1, load=100.0, reps=5, rpe=8.5))
    (from_log,) = [r for r in await _rows(async_db, worker.id) if r.source == "workout_extraction"]

    reporter = User(email=f"par-a-{flag}@test.com", hashed_password="x", is_active=True)
    async_db.add(reporter)
    await async_db.commit()
    await async_db.refresh(reporter)
    await ses.record_strength_report(async_db, reporter.id, _report(), collection_mode="retest")
    (from_assess,) = await _rows(async_db, reporter.id)

    assert (from_log.raw_value, from_log.formula, from_log.model_version) == (
        from_assess.raw_value, from_assess.formula, from_assess.model_version
    )
    assert from_log.formula == (sc.FORMULA_CHART if flag else sc.FORMULA_EPLEY)
    assert ses.derived_value(_report()) == from_assess.raw_value  # and the onboarding seed's number
    assert ses.seed_values([_report()])[CODE] == from_assess.raw_value


async def test_a_chart_row_carries_the_model_version_and_a_legacy_row_does_not(async_db, chart, monkeypatch):
    user = await _athlete(async_db, "ver@test.com")
    await process_new_workout(async_db, user.id, _session(T1))
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", False)
    await process_new_workout(async_db, user.id, _session(T1 + timedelta(days=1)))
    new, old = (await _rows(async_db, user.id))[:2]
    assert (new.formula, new.model_version) == ("rpe_rir_chart", sc.MODEL_VERSION)
    assert (old.formula, old.model_version) == ("epley", None)


# ----- semantics and authority --------------------------------------------------------------------------- #

def test_a_modeled_estimate_has_no_capacity_authority_whatever_its_source():
    for mode in (oa.CM_RETEST, oa.CM_AD_HOC, oa.CM_WORKOUT, oa.CM_ONBOARDING_ONRAMP):
        for source_type in (oa.ST_ATHLETE_ENTRY, oa.ST_WORKOUT_EXTRACTION):
            result = oa.resolve_authority(
                source_type=source_type, collection_mode=mode, evidence_type=se.EV_MODELED_ESTIMATE,
                value_semantics=se.VS_ESTIMATED, protocol_validity=oa.PV_VALID,
            )
            assert result.capacity_effect == oa.CE_NONE, (mode, source_type)
    # …while the legacy estimate keeps its upward-floor authority (history is unchanged).
    legacy = oa.resolve_authority(
        source_type=oa.ST_WORKOUT_EXTRACTION, collection_mode=oa.CM_WORKOUT,
        evidence_type=se.EV_ESTIMATED_FROM_TRAINING_SET, value_semantics=se.VS_ESTIMATED,
        protocol_validity=oa.PV_NOT_EVALUATED,
    )
    assert legacy.capacity_effect == oa.CE_UPWARD_LOWER_BOUND


async def test_chart_extraction_is_modeled_pr_or_not_and_records_no_floor_and_no_state(async_db, chart):
    user = await _athlete(async_db, "sem@test.com")
    await process_new_workout(async_db, user.id, _session(T1, load=120.0, reps=3, rpe=9.0))  # a PR
    await process_new_workout(async_db, user.id, _session(T1 + timedelta(days=1), load=100.0, reps=3, rpe=9.0))  # not
    pr, below = [r for r in await _rows(async_db, user.id) if r.source == "workout_extraction"]

    for r in (pr, below):
        assert (r.evidence_type, r.value_semantics) == ("modeled_estimate", "estimated")
        assert r.capacity_effect == "none" and r.affects_prescription is True
        assert (r.formula, r.model_version) == ("rpe_rir_chart", sc.MODEL_VERSION)
    assert (pr.observation_weight, below.observation_weight) == (0.10, 0.0)  # PR tracking is kept as metadata
    assert await _count(async_db, CapacityFloorShadowLog) == 0


async def test_the_legacy_estimate_keeps_its_labels_when_the_flag_is_off(async_db):
    user = await _athlete(async_db, "legacy-sem@test.com")
    await process_new_workout(async_db, user.id, _session(T1, load=120.0, reps=3, rpe=9.0))
    (row,) = [r for r in await _rows(async_db, user.id) if r.source == "workout_extraction"]
    assert (row.evidence_type, row.value_semantics, row.capacity_effect) == (
        "lower_bound", "lower_bound", "upward_lower_bound"
    )


async def test_a_chart_estimate_reported_in_assess_is_modeled_and_authorityless(async_db, chart):
    user = await _athlete(async_db, "assess-sem@test.com")
    await ses.record_strength_report(async_db, user.id, _report(), collection_mode="retest")
    (row,) = await _rows(async_db, user.id)
    assert (row.evidence_type, row.value_semantics, row.capacity_effect) == ("modeled_estimate", "estimated", "none")
    assert await _count(async_db, CapacityFloorShadowLog) == 0


# ----- retry identity ------------------------------------------------------------------------------------- #

@pytest.mark.parametrize(("first", "second"), [(False, True), (True, False)])
async def test_a_submission_retried_under_the_other_formula_adds_nothing(async_db, monkeypatch, first, second):
    """The retry guard matches what was SUBMITTED, never what was derived from it: an old Epley
    submission retried after the chart switch (and the reverse) is recognised."""
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", first)
    user = await _athlete(async_db, f"retry-{first}@test.com")
    report = _report()
    await ses.check_reports(async_db, [report])
    await ses.stage_strength_report(async_db, user.id, report, collection_mode="onboarding_onramp", skip_if_recorded=True)
    await async_db.commit()
    rows_before = await _rows(async_db, user.id)
    states_before = await _count(async_db, AthleteState)
    profile = await async_db.get(AthleteProfile, user.id) or (
        await async_db.execute(select(AthleteProfile).where(AthleteProfile.user_id == user.id))
    ).scalar_one()
    projected_before = profile.squat_1rm

    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", second)
    again = await ses.stage_strength_report(
        async_db, user.id, report, collection_mode="onboarding_onramp", skip_if_recorded=True
    )
    await async_db.commit()

    assert again is None  # recognised, not staged
    assert [r.id for r in await _rows(async_db, user.id)] == [r.id for r in rows_before]
    assert await _count(async_db, AthleteState) == states_before
    await async_db.refresh(profile)
    assert profile.squat_1rm == projected_before  # and the projection did not move


async def test_a_different_report_is_still_a_new_report(async_db, chart):
    user = await _athlete(async_db, "retry-diff@test.com")
    await ses.stage_strength_report(async_db, user.id, _report(), collection_mode="retest", skip_if_recorded=True)
    await async_db.commit()
    other = await ses.stage_strength_report(
        async_db, user.id, _report(rpe=9.0), collection_mode="retest", skip_if_recorded=True
    )
    assert other is not None


async def test_tested_max_and_estimate_retries_are_matched_as_before(async_db):
    user = await _athlete(async_db, "retry-max@test.com")
    tested = StrengthReport(benchmark_code=CODE, method="tested_max", value_kg=150.0, performed_at=T1)
    assert await ses.stage_strength_report(async_db, user.id, tested, collection_mode="retest", skip_if_recorded=True)
    await async_db.commit()
    assert await ses.stage_strength_report(async_db, user.id, tested, collection_mode="retest", skip_if_recorded=True) is None
    heavier = StrengthReport(benchmark_code=CODE, method="tested_max", value_kg=155.0, performed_at=T1)
    assert await ses.stage_strength_report(async_db, user.id, heavier, collection_mode="retest", skip_if_recorded=True)


# ----- coexistence: each reader's policy with mixed formulas ----------------------------------------------- #

async def _basis(db, user_id: int, as_of: datetime) -> float | None:
    rx = WorkoutPrescription(
        type="strength", focus="squat", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE")],
    )
    await _enrich_exercises_with_load(db, user_id, rx, {"week_number": 1, "duration_weeks": 4}, as_of=as_of)
    return rx.exercises[0].e1rm_basis_kg


async def test_prescription_takes_the_highest_eligible_value_whatever_formula_made_it(async_db, monkeypatch):
    """Policy: the selector is formula-blind within the window; a chart estimate above an Epley one
    sizes the load (the step the activation report shows). Both rows qualify equally."""
    user = await _athlete(async_db, "coex-rx@test.com")
    await process_new_workout(async_db, user.id, _session(T1, load=100.0, reps=5, rpe=8.5))  # Epley
    epley = sc.e1rm_from_set(100.0, 5)
    assert await _basis(async_db, user.id, T1 + timedelta(days=1)) == pytest.approx(epley)

    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", True)
    await process_new_workout(async_db, user.id, _session(T1 + timedelta(days=1), load=100.0, reps=5, rpe=8.5))
    chart_value = sc.estimate_e1rm(load_kg=100.0, reps=5, rpe=8.5, rir=None, use_chart=True).value

    assert chart_value > epley
    assert await _basis(async_db, user.id, T1 + timedelta(days=2)) == pytest.approx(chart_value)  # one step, at once


async def test_the_dose_denominator_is_the_same_number_the_prescription_uses(async_db, chart):
    from app.services.state_service import prelog_e1rm_denominators

    user = await _athlete(async_db, "coex-dose@test.com")
    await process_new_workout(async_db, user.id, _session(T1))
    denom = (await prelog_e1rm_denominators(async_db, user.id, {CODE}, as_of=T1 + timedelta(days=1)))[CODE]
    assert denom["value"] == pytest.approx(await _basis(async_db, user.id, T1 + timedelta(days=1)))


async def test_pr_tracking_compares_estimates_only_with_estimates_of_the_same_formula(async_db, chart, monkeypatch):
    user = await _athlete(async_db, "coex-pr@test.com")
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", False)
    await process_new_workout(async_db, user.id, _session(T1, load=130.0, reps=3, rpe=9.0))  # a high Epley row
    monkeypatch.setattr(settings, "E1RM_CHART_ESTIMATES", True)

    assert await estimated_pr_baseline(async_db, user.id, CODE, formula=sc.FORMULA_CHART) is None
    await process_new_workout(async_db, user.id, _session(T1 + timedelta(days=1), load=100.0, reps=3, rpe=9.0))
    chart_row = [r for r in await _rows(async_db, user.id) if r.formula == "rpe_rir_chart"][0]
    assert chart_row.observation_weight == 0.10  # first estimate of its formula is a PR, though below the Epley row


# ----- the activation report -------------------------------------------------------------------------------- #

async def test_the_activation_report_shows_each_athletes_step_and_writes_nothing(async_db, monkeypatch):
    user = await _athlete(async_db, "act@test.com")
    await process_new_workout(async_db, user.id, _session(T1, load=100.0, reps=5, rpe=8.5))  # Epley (flag off)
    epley = sc.e1rm_from_set(100.0, 5)
    chart_value = sc.estimate_e1rm(load_kg=100.0, reps=5, rpe=8.5, rir=None, use_chart=True).value
    before = (await _count(async_db, BenchmarkObservation), await _count(async_db, AthleteState))

    (line,) = await activation_report(async_db, as_of=T1.replace(tzinfo=None) + timedelta(days=1))

    assert (line.user_id, line.code, line.restated_rows) == (user.id, CODE, 1)
    assert line.basis_now_kg == pytest.approx(epley) and line.basis_chart_kg == pytest.approx(chart_value)
    assert line.step_pct == pytest.approx((chart_value / epley - 1) * 100)
    assert line.relative_load_factor == pytest.approx(epley / chart_value)  # the dose ladder's load / e1rm_pre shrinks by this
    assert (await _count(async_db, BenchmarkObservation), await _count(async_db, AthleteState)) == before


async def test_the_activation_report_does_not_restate_what_the_chart_already_estimated_or_cannot(async_db, chart):
    user = await _athlete(async_db, "act2@test.com")
    await process_new_workout(async_db, user.id, _session(T1, load=100.0, reps=5, rpe=8.5))  # already chart
    (line,) = await activation_report(async_db, as_of=T1.replace(tzinfo=None) + timedelta(days=1))
    assert line.restated_rows == 0 and line.step_pct == pytest.approx(0.0)


# ----- the chart only speaks for sets that can size a load ------------------------------------------------ #

@pytest.mark.parametrize(
    ("reps", "rpe"),
    [(12, 6.0), (8, 9.0), (6, 9.0), (5, 7.0), (3, 7.5)],
)
def test_a_set_that_cannot_size_a_load_keeps_the_legacy_estimate_even_with_the_flag_on(chart, reps, rpe):
    """The chart is 16-27% above Epley at high reps or easy efforts. Those sets fail the extraction
    gate, so they never size a load; letting the chart re-estimate them would only move the
    profile projection and the onboarding seed by the largest steps."""
    from app.services.e1rm_estimation import estimate_set_e1rm

    est = estimate_set_e1rm(100.0, reps, rpe, None)
    assert (est.formula, est.value) == (sc.FORMULA_EPLEY, sc.e1rm_from_set(100.0, reps))
    report = _report(reps=reps, rpe=rpe)
    assert ses.derived_value(report) == sc.e1rm_from_set(100.0, reps)
    assert ses.seed_values([report])[CODE] == sc.e1rm_from_set(100.0, reps)


def test_a_set_that_clears_the_gate_gets_the_chart_estimate(chart):
    from app.services.e1rm_estimation import estimate_set_e1rm

    est = estimate_set_e1rm(100.0, 5, 8.0, None)
    assert est.formula == sc.FORMULA_CHART and est.value > sc.e1rm_from_set(100.0, 5)
    # weaker effort provenance needs the stricter bar, exactly as the extraction gate does
    weak = estimate_set_e1rm(100.0, 5, 8.0, None, "group_level")
    assert weak.formula == sc.FORMULA_EPLEY


async def test_an_assess_report_that_cannot_size_a_load_is_recorded_as_epley_with_the_flag_on(async_db, chart):
    user = await _athlete(async_db, "gate-assess@test.com")
    await ses.record_strength_report(async_db, user.id, _report(reps=12, rpe=6.0), collection_mode="retest")
    (row,) = await _rows(async_db, user.id)
    assert (row.formula, row.evidence_type) == ("epley", "estimated_from_training_set")
    assert row.raw_value == sc.e1rm_from_set(100.0, 12)


async def test_the_activation_report_restates_only_sets_the_chart_would_speak_for(async_db):
    user = await _athlete(async_db, "act3@test.com")
    as_of = T1.replace(tzinfo=None) + timedelta(days=1)
    # flag off: an easy high-rep set is recorded as Epley; it cannot size a load, so it is no basis
    await ses.record_strength_report(async_db, user.id, _report(reps=12, rpe=6.0), collection_mode="retest")
    assert await activation_report(async_db, as_of=as_of) == []
    # a gate-passing set is restated; the high-rep one still is not
    await process_new_workout(async_db, user.id, _session(T1, load=100.0, reps=5, rpe=8.5))
    (line,) = await activation_report(async_db, as_of=as_of)
    assert line.restated_rows == 1
