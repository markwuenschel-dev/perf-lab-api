"""A prescription must carry the evidence behind its claims and its own certainty.

Two of the eight explanations the twin is supposed to deliver were absent from the
response contract: *what evidence supports this* and *how confident is it*.
``state_drivers`` collapsed each threshold test to a phrase and threw away the number that
fired it, and no field anywhere carried a variance, interval, or confidence band — grepping
``confidence|variance|uncertain|interval`` over app/schemas/prescription.py returned nothing.

Both were cheap because the data was already in hand: ``finalize_prescription`` receives the
whole state vector, and ``_derive_state_drivers`` already read the exact numeric behind each
label before discarding it.
"""

from datetime import UTC, datetime

import pytest
from state_axes import split_legacy, with_axes

from app.domain.vectors import CapacityConfidence
from app.logic.confidence_presentation import POLICY_VERSION
from app.logic.prescription_finalize import (
    EXPECTED_OUTCOME_HORIZON,
    MAX_EXPECTED_OUTCOMES,
    MAX_INACTIVE_TRIGGERS,
    MIN_REPORTABLE_DELTA,
    NO_DRIVERS_LABEL,
    UNCERTAINTY_NOT_MODELLED,
    _derive_confidence,
    _derive_expected_outcomes,
    _derive_measurement_recommendations,
    _derive_plan_revision_triggers,
    _derive_state_drivers,
    _derive_state_evidence,
    finalize_prescription,
)
from app.schemas.state import UnifiedStateVector


def _state(**over: object) -> UnifiedStateVector:
    axes, rest = split_legacy(over)
    base: dict[str, object] = {
        "timestamp": datetime.now(UTC),
        "c_met_aerobic": 500.0,
        "c_nm_force": 50.0,
        "c_struct": 50.0,
        "b_met_anaerobic": 50.0,
    }
    base.update(rest)
    return with_axes(UnifiedStateVector(**base), **{"c_met_aerobic": 500.0, **axes})  # type: ignore[arg-type]


# ── evidence carries the number, not just the phrase ──────────────────────────


def test_evidence_carries_the_value_and_threshold_that_fired_it() -> None:
    ev = _derive_state_evidence(_state(f_nm_central=70.0))

    assert len(ev) == 1
    e = ev[0]
    assert e.axis == "f_nm_central"
    assert e.label == "elevated CNS / central fatigue"
    assert e.value == 70.0
    assert e.threshold == 55.0
    assert e.direction == "above"


def test_drivers_and_evidence_can_never_disagree() -> None:
    """Both are emitted from one rule table, so the phrase always matches the number.

    They were previously eight independent ``if`` statements producing only strings; a
    threshold edited in one place and not the other could not be detected.
    """
    state = _state(f_nm_central=70.0, f_met_systemic=80.0, c_met_aerobic=20.0)

    drivers = _derive_state_drivers(state)
    evidence = _derive_state_evidence(state)

    assert drivers == [e.label for e in evidence]
    for e in evidence:
        if e.direction == "above":
            assert e.value > e.threshold
        else:
            assert e.value < e.threshold


def test_no_driver_fires_means_empty_evidence_not_an_invented_observation() -> None:
    """The "all normal" sentinel is a driver-only phrase; evidence stays empty.

    Emitting a synthetic evidence row for "nothing fired" would be exactly the
    missing-becomes-a-number failure this codebase is trying to stop.
    """
    state = _state()

    assert _derive_state_drivers(state) == [NO_DRIVERS_LABEL]
    assert _derive_state_evidence(state) == []


def test_a_two_sided_rule_does_not_fire_on_an_unpopulated_axis() -> None:
    """c_met_aerobic == 0.0 means "no aerobic signal yet", not "no aerobic capacity"."""
    assert _derive_state_evidence(_state(c_met_aerobic=0.0)) == []
    assert _derive_state_evidence(_state(c_met_aerobic=20.0))[0].axis == "c_met_aerobic"


# ── certainty, including the certainty that is missing ────────────────────────


def test_capacity_backed_driver_carries_its_confidence_band() -> None:
    """The rule keeps the legacy axis id; it reads, and its variance lives, under the decomposed name.

    ``c_met_aerobic`` vs ``capacity_confidence.aerobic`` — matching on the axis name alone
    would silently report "no uncertainty modelled" for the one driver that has some, and
    nothing else in the payload would look wrong. This pins the declared mapping.
    """
    ev = _derive_state_evidence(_state(c_met_aerobic=20.0))

    assert len(ev) == 1
    assert ev[0].confidence_status is not None


def test_unmodelled_axes_report_null_certainty_not_high_certainty() -> None:
    """Fatigue and tissue carry no variance anywhere in the engine.

    Reporting None (documented as UNKNOWN) rather than omitting the field keeps a reader
    from taking silence for confidence.
    """
    ev = _derive_state_evidence(_state(f_nm_central=70.0, tissue_t={"lumbar": 60.0}))

    assert {e.axis for e in ev} == {"f_nm_central", "tissue_t.lumbar"}
    assert all(e.confidence_status is None for e in ev)


def test_confidence_names_the_state_families_it_cannot_model() -> None:
    c = _derive_confidence(_state())

    assert set(c.uncertainty_not_modelled) == set(UNCERTAINTY_NOT_MODELLED)
    assert "fatigue_f" in c.uncertainty_not_modelled
    assert "tissue_t" in c.uncertainty_not_modelled


def test_confidence_covers_every_capacity_axis() -> None:
    c = _derive_confidence(_state())

    assert set(c.capacity_axes) == set(CapacityConfidence.KEYS)


def test_confidence_reuses_the_shared_policy_version() -> None:
    """The band thresholds must not be re-declared here.

    confidence_presentation.py owns them precisely so consumers cannot drift; if this
    stops matching, some caller has started deciding certainty on its own.
    """
    assert _derive_confidence(_state()).policy_version == POLICY_VERSION


def test_weakest_axis_is_the_least_certain_one() -> None:
    """The weakest axis is what should most limit trust in the prescription."""
    conf = CapacityConfidence()
    # Established (low variance) everywhere except one deliberately starved axis.
    for axis in CapacityConfidence.KEYS:
        setattr(conf, axis, 0.05)
    conf.max_strength = 1.4  # above PROVISIONAL_MAX_VARIANCE -> insufficient

    c = _derive_confidence(_state(capacity_confidence=conf))

    assert c.weakest_capacity_axis == "max_strength"
    assert c.weakest_capacity_status == "insufficient"
    assert c.capacity_axes["aerobic"] == "established"


# ── measurement recommendations ───────────────────────────────────────────────


def _conf(**over: float) -> CapacityConfidence:
    """All axes established except the ones named."""
    c = CapacityConfidence()
    for axis in CapacityConfidence.KEYS:
        setattr(c, axis, 0.05)
    for axis, var in over.items():
        setattr(c, axis, var)
    return c


def test_established_axes_are_never_suggested_for_measurement() -> None:
    """Nothing is gained by re-measuring an axis the twin already knows."""
    recs = _derive_measurement_recommendations(_state(capacity_confidence=_conf()), "powerlifting")

    assert recs == []


def test_goal_relevant_uncertainty_outranks_more_severe_irrelevant_uncertainty() -> None:
    """This is the whole point of ranking: relevance beats raw severity.

    mobility is *more* uncertain than hypertrophy, but powerlifting does not train it, so
    the axis the athlete actually loads is the one worth measuring first.
    """
    state = _state(
        capacity_confidence=_conf(max_strength=1.4, hypertrophy=0.9, mobility=1.4)
    )

    recs = _derive_measurement_recommendations(state, "powerlifting")
    axes = [r.axis for r in recs]

    assert axes[0] == "max_strength"
    assert axes.index("hypertrophy") < axes.index("mobility")
    assert [r.material_to_goal for r in recs][:2] == [True, True]


def test_every_recommendation_explains_why() -> None:
    state = _state(capacity_confidence=_conf(max_strength=1.4, hypertrophy=0.9))

    for r in _derive_measurement_recommendations(state, "powerlifting"):
        assert r.reason, f"{r.axis} recommended with no reason"
        assert r.current_status != "established"


def test_an_unrecognised_goal_still_recommends_by_severity() -> None:
    """No goal domain means no relevance signal — rank on certainty alone, never crash."""
    state = _state(capacity_confidence=_conf(max_strength=1.4, hypertrophy=0.9))

    recs = _derive_measurement_recommendations(state, "not-a-real-goal")

    assert [r.axis for r in recs] == ["max_strength", "hypertrophy"]
    assert all(r.material_to_goal is False for r in recs)


# ── what would change the plan ────────────────────────────────────────────────


def test_an_active_driver_yields_a_switch_off_trigger() -> None:
    """A driver that is firing should say what would stop it applying."""
    triggers = _derive_plan_revision_triggers(_state(f_nm_central=70.0))
    active = [t for t in triggers if t.currently_active]

    assert len(active) == 1
    t = active[0]
    assert t.axis == "f_nm_central"
    assert t.current_value == 70.0
    assert t.threshold == 55.0
    assert "falls back to 55" in t.condition


def test_the_nearest_approaching_driver_ranks_first() -> None:
    """Among drivers not yet firing, the one closest to its threshold matters most."""
    triggers = _derive_plan_revision_triggers(_state(tissue_t={"knee": 53.0}))
    inactive = [t for t in triggers if not t.currently_active]

    assert inactive[0].axis == "tissue_t.knee"
    assert "rises above 55" in inactive[0].condition


def test_an_unpopulated_axis_has_no_threshold_to_watch() -> None:
    """c_met_aerobic == 0.0 is "no signal", so there is no meaningful crossing.

    Reporting "your aerobic capacity would have to fall below 30" for an athlete who has
    never run would be inventing a watch on a number that does not exist. The honest ask
    there is a measurement, and measurement_recommendations already carries it.
    """
    triggers = _derive_plan_revision_triggers(_state(c_met_aerobic=0.0))

    assert all(t.axis != "c_met_aerobic" for t in triggers)


def test_a_populated_aerobic_axis_does_get_a_trigger() -> None:
    """The skip above must be about being unpopulated, not about the axis itself."""
    triggers = _derive_plan_revision_triggers(_state(c_met_aerobic=35.0))

    assert any(t.axis == "c_met_aerobic" for t in triggers)


def test_triggers_never_contradict_the_thresholds_that_produced_the_session() -> None:
    """Same rule table as the drivers, so a trigger cannot disagree with the reasoning.

    An active trigger must be on the firing side of its threshold; an inactive one must not.
    """
    state = _state(f_nm_central=70.0, f_met_systemic=80.0, tissue_t={"knee": 53.0})

    for t in _derive_plan_revision_triggers(state):
        beyond = t.current_value > t.threshold
        assert t.currently_active == beyond, (
            f"{t.axis}: active={t.currently_active} but value {t.current_value} "
            f"vs threshold {t.threshold}"
        )


def test_approaching_triggers_are_capped_but_active_ones_are_never_dropped() -> None:
    """All active drivers are shown; only the approaching list is truncated."""
    state = _state(
        f_nm_central=70.0,
        f_nm_peripheral=70.0,
        f_met_systemic=80.0,
        tissue_t={"lumbar": 60.0, "wrist": 60.0, "knee": 60.0},
    )

    triggers = _derive_plan_revision_triggers(state)
    active = [t for t in triggers if t.currently_active]
    inactive = [t for t in triggers if not t.currently_active]

    assert len(active) == 6, [t.axis for t in active]
    assert len(inactive) <= MAX_INACTIVE_TRIGGERS


# ── expected outcomes: a real forecast, or none at all ────────────────────────


def _candidate_rx(state):
    """A finalized prescription built the way the prescriber builds one."""
    from app.logic.prescriber import recommend_next_session

    return recommend_next_session(state, "powerlifting")


def test_a_session_carries_a_forecast_from_the_engines_own_model() -> None:
    rx = _candidate_rx(_state())

    assert rx.why is not None
    assert rx.why.expected_outcomes, "no forecast produced"
    assert all(o.delta != 0 for o in rx.why.expected_outcomes)


def test_the_forecast_matches_an_independent_forward_step() -> None:
    """Pins that this IS the engine's forward model, not a display-only reimplementation.

    Recomputing the same one-step rollout must reproduce the published numbers exactly. If
    these ever diverge, the athlete is shown a forecast the planner does not share - which
    would make the loop's later prediction-error measurement meaningless.
    """
    from datetime import timedelta

    from app.logic.constraint_engine.candidate import SessionCandidate
    from app.logic.dose_engine_v0 import calculate_stress_dose
    from app.logic.mpc.candidate_dose import candidate_to_log
    from app.logic.state_update_v0 import update_athlete_state

    state = _state()
    candidate = SessionCandidate(
        type="Strength", focus="Squat", rationale="r", duration_min=60, branch_id="t"
    )

    published = _derive_expected_outcomes(state, candidate)
    assert published, "no forecast produced for a valid candidate"

    log = candidate_to_log(candidate, state.timestamp)
    expected_state = update_athlete_state(state, calculate_stress_dose(log), timedelta(0), log)

    for o in published:
        key = o.axis.split(".", 1)[1]
        assert o.predicted == pytest.approx(
            round(float(getattr(expected_state.fatigue_f, key)), 3)
        ), o.axis


def test_outcomes_are_largest_movement_first_and_capped() -> None:
    rx = _candidate_rx(_state())
    assert rx.why is not None
    deltas = [abs(o.delta) for o in rx.why.expected_outcomes]

    assert deltas == sorted(deltas, reverse=True)
    assert len(rx.why.expected_outcomes) <= MAX_EXPECTED_OUTCOMES


def test_every_reported_movement_clears_the_noise_floor() -> None:
    """A sub-threshold wiggle is engine noise, not a claim worth publishing."""
    rx = _candidate_rx(_state())
    assert rx.why is not None

    for o in rx.why.expected_outcomes:
        assert abs(o.delta) >= MIN_REPORTABLE_DELTA


def test_the_horizon_is_always_stated() -> None:
    """A one-step prediction must not be readable as a longer-range claim."""
    rx = _candidate_rx(_state())
    assert rx.why is not None

    assert rx.why.expected_outcome_horizon == EXPECTED_OUTCOME_HORIZON
    assert "after this session" in rx.why.expected_outcome_horizon


def test_without_a_candidate_there_is_no_forecast_rather_than_a_guess() -> None:
    """finalize_prescription's older signature still works, and degrades honestly.

    A missing forward-model input yields no prediction at all - never an invented one.
    """
    from app.schemas.prescription import ExercisePrescription, WorkoutPrescription

    rx = WorkoutPrescription(
        type="Strength", focus="Squat", rationale="r", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=5, reps="5")],
    )
    out = finalize_prescription(rx, _state(), "powerlifting", "b")

    assert out.why is not None
    assert out.why.expected_outcomes == []


def test_a_failing_forecast_does_not_break_the_prescription(monkeypatch) -> None:
    """An explanation must never be a reason to fail the session it explains."""
    import app.logic.mpc.candidate_dose as cd

    def _boom(*_a, **_k):
        raise RuntimeError("forward model exploded")

    monkeypatch.setattr(cd, "candidate_to_log", _boom)
    rx = _candidate_rx(_state())

    assert rx.why is not None
    assert rx.why.expected_outcomes == []
    assert rx.type, "the prescription itself must survive"


# ── the engine version is produced, not merely defaulted ──────────────────────


def test_prescription_engine_version_is_assigned_not_defaulted() -> None:
    """The published version must come from the engine, not from a schema default.

    It was a bare "v0.3" literal in the Field default that nothing ever assigned, so the
    advertised "engine version" was a constant that could not move however much the
    prescriber changed. Assigning it means a prescription reports what produced it.
    """
    from app.logic.prescriber import recommend_next_session
    from app.schemas.prescription import PRESCRIPTION_ENGINE_VERSION

    rx = recommend_next_session(_state(), "powerlifting")

    assert rx.model_version == PRESCRIPTION_ENGINE_VERSION


def test_the_no_state_path_also_reports_the_engine_version() -> None:
    """The hard-override path builds a fresh prescription object and must still stamp it."""
    from app.schemas.prescription import (
        PRESCRIPTION_ENGINE_VERSION,
        ExercisePrescription,
        WorkoutPrescription,
    )

    rx = WorkoutPrescription(
        type="Strength", focus="Squat", rationale="r", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=5, reps="5")],
    )
    out = finalize_prescription(rx, None, "powerlifting", "b")

    assert out.model_version == PRESCRIPTION_ENGINE_VERSION


def test_bumping_the_engine_version_is_flagged_as_cross_language() -> None:
    """Records a coupling that lives nowhere else in Python.

    The web contract test pins the literal value, so a bump that only changes the Python
    constant breaks the frontend suite with no hint of why. If this fails, update
    web/src/perflab/screens/overview/todayPrescriptionContract.test.ts and openapi.json in
    the same change.
    """
    from pathlib import Path

    from app.schemas.prescription import PRESCRIPTION_ENGINE_VERSION

    contract = (
        Path(__file__).resolve().parents[1]
        / "web/src/perflab/screens/overview/todayPrescriptionContract.test.ts"
    )
    if not contract.exists():  # pragma: no cover - web tree not always present
        return

    assert f'model_version: "{PRESCRIPTION_ENGINE_VERSION}"' in contract.read_text(
        encoding="utf-8"
    ), (
        "the web contract test pins a different model_version than the Python constant - "
        "bump both together, plus openapi.json"
    )
