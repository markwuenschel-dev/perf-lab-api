"""Prescription finalize + template linkage."""

from datetime import UTC, datetime

from app.logic.prescriber import recommend_next_session
from app.logic.prescription_finalize import finalize_prescription
from app.schemas.prescription import WorkoutPrescription
from app.schemas.state import UnifiedStateVector


def _healthy_state() -> UnifiedStateVector:
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        c_met_aerobic=50.0,
        c_nm_force=50.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
        f_met_systemic=20.0,
        f_nm_peripheral=15.0,
        f_nm_central=20.0,
        f_struct_damage=10.0,
        s_struct_signal=20.0,
        habit_strength=0.6,
        skill_state={"squat": 0.7},
    )


def test_recommend_includes_why_and_template():
    rx = recommend_next_session(_healthy_state(), goal="Running")
    assert rx.why is not None
    assert rx.why.template_id == "tmpl_run_hinshaw_style_v1"
    assert rx.why.validation is not None
    assert rx.why.validation.passed is True


def test_finalize_no_state_assessment():
    rx = finalize_prescription(
        WorkoutPrescription(
            type="Assessment",
            focus="x",
            rationale="y",
            duration_min=60,
        ),
        None,
        "Strength",
        "no_athlete_state",
    )
    assert rx.why is not None
    assert "No AthleteState" in rx.why.state_drivers[0]


def test_gymnastics_overrides_on_wrist_stress():
    s = _healthy_state()
    s.tissue_t.wrist = 80.0
    rx = recommend_next_session(s, goal="Gymnastics")
    assert rx.type == "Recovery"
    assert rx.why is not None
    assert rx.why.validation is not None
    assert rx.why.validation.hard_violations


def test_deload_scales_prescribed_duration():
    state = _healthy_state()
    base = recommend_next_session(state, goal="Running")
    assert base.duration_min > 0

    # is_deload does not change candidate selection, only post-finalize volume.
    deload = recommend_next_session(
        state,
        goal="Running",
        block_context={"is_deload": True, "deload_volume_factor": 0.5},
    )
    assert deload.duration_min == max(1, round(base.duration_min * 0.5))
    assert deload.duration_min < base.duration_min
    assert deload.why is not None
    assert any(c.startswith("block:deload") for c in deload.why.constraints_applied)


def test_deload_uses_default_factor_when_unspecified():
    state = _healthy_state()
    base = recommend_next_session(state, goal="Running")
    deload = recommend_next_session(
        state,
        goal="Running",
        block_context={"is_deload": True},
    )
    assert deload.duration_min == max(1, round(base.duration_min * 0.6))
