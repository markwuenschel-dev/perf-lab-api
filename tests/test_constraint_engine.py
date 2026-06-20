"""Constraint engine + scorer (mock context)."""

from datetime import UTC, datetime

from app.logic.coaching_template_registry import get_structured_template_by_id
from app.logic.constraint_engine import (
    SessionValidator,
    build_constraint_context,
    encode_session_candidate,
    simple_session_scorer,
)
from app.schemas.prescription import WorkoutPrescription
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
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


def test_session_validator_degraded_empty_history():
    tpl = get_structured_template_by_id("tmpl_run_hinshaw_style_v1")
    assert tpl is not None
    rx = WorkoutPrescription(
        type="Aerobic Base",
        focus="Easy Run",
        rationale="x",
        duration_min=40,
    )
    candidate = encode_session_candidate(rx, "Running", "t", None)
    ctx = build_constraint_context(_state(), [], "Running")
    rep = SessionValidator(tpl).validate(candidate, ctx)
    assert rep.ok
    assert not rep.hard_failed


def test_simple_session_scorer_in_range():
    tpl = get_structured_template_by_id("tmpl_run_hinshaw_style_v1")
    assert tpl is not None
    rx = WorkoutPrescription(
        type="Aerobic Base",
        focus="Easy Run",
        rationale="x",
        duration_min=45,
    )
    c = encode_session_candidate(rx, "Running", "b", None)
    s = simple_session_scorer(c, tpl, _state())
    assert 0.0 <= s <= 1.0
