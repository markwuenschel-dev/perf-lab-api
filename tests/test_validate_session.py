"""Constraint validation tests."""

from datetime import UTC, datetime

from app.logic.validate_session import validate_session
from app.schemas.program_template import ProgramTemplate
from app.schemas.session_draft import SessionDraft
from app.schemas.state import UnifiedStateVector


def _minimal_state(**kwargs: float) -> UnifiedStateVector:
    base = UnifiedStateVector(
        timestamp=datetime.now(UTC),
        c_met_aerobic=50.0,
        c_nm_force=50.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
        f_met_systemic=0.0,
        f_nm_peripheral=0.0,
        f_nm_central=0.0,
        f_struct_damage=0.0,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_gymnastics_wrist_hard_violation():
    state = _minimal_state()
    state.tissue_t.wrist = 80.0
    draft = SessionDraft(session_kind="gymnastics_skill", intensity_band="moderate")
    tpl = ProgramTemplate(
        id="t",
        name="t",
        domain="gymnastics",
        goals=["Gymnastics"],
        source_name="test",
    )
    v, soft, hard = validate_session(draft, state, "Gymnastics", tpl)
    assert "gymnastics_wrist_tissue" in hard
    assert v.passed is False


def test_olympic_soft_when_systemic_high():
    state = _minimal_state()
    state.f_met_systemic = 70.0
    draft = SessionDraft(
        session_kind="olympic",
        metabolic_emphasis=0.6,
        technical_emphasis=0.9,
    )
    tpl = ProgramTemplate(
        id="t",
        name="t",
        domain="wl",
        goals=["OlympicLifts"],
        source_name="test",
    )
    v, soft, hard = validate_session(draft, state, "OlympicLifts", tpl)
    assert hard == []
    assert any("olympic_metabolic" in s for s in soft)
