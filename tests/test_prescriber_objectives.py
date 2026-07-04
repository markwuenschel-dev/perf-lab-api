"""Prescriber objective taper + domain-emphasis (Phase 4a — goal-anchored
program plan).

Non-DB: calls `recommend_next_session` directly with a `block_context` dict
carrying the two signals `app.services.objective_service.active_objective_signals`
produces (`objective_taper`, `objective_domain`) — mirrors the Phase 3a tests
in test_prescriber_session_prefs.py.
"""
from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.prescriber import OBJECTIVE_TAPER_FACTOR, recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

# Neutral "Powerlifting" state: winning candidate is "SBD Strength"
# (branch pl_sbd_main), duration_min=80 — see test_prescriber_session_prefs.py.
_TEMPLATE_DURATION = 80


def _neutral_state(*, cns: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState(cns=cns)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={"squat": 0.5},
        **leg,
    )


# ---------------------------------------------------------------------------
# Taper
# ---------------------------------------------------------------------------

def test_objective_taper_scales_duration_and_annotates():
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", block_context={"objective_taper": True}
    )
    assert rx.duration_min == round(_TEMPLATE_DURATION * OBJECTIVE_TAPER_FACTOR)
    assert rx.why is not None
    assert any(c.startswith("objective:taper") for c in rx.why.constraints_applied)


def test_objective_taper_false_leaves_duration_unchanged():
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", block_context={"objective_taper": False}
    )
    assert rx.duration_min == _TEMPLATE_DURATION
    assert rx.why is not None
    assert not any(c.startswith("objective:taper") for c in rx.why.constraints_applied)


def test_objective_taper_composes_after_target_duration_override():
    """Taper is the last word on duration — it scales down whatever
    periodization/block-target preferences already settled on."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"objective_taper": True, "target_session_minutes": 90},
    )
    assert rx.duration_min == round(90 * OBJECTIVE_TAPER_FACTOR)


# ---------------------------------------------------------------------------
# Domain emphasis
# ---------------------------------------------------------------------------

def test_objective_domain_emphasis_annotates_when_winner_matches():
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", block_context={"objective_domain": "powerlifting"}
    )
    assert rx.why is not None
    assert "objective:domain_emphasis=powerlifting" in rx.why.constraints_applied


def test_objective_domain_emphasis_absent_when_no_match():
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", block_context={"objective_domain": "running"}
    )
    assert rx.why is not None
    assert not any(c.startswith("objective:domain_emphasis") for c in rx.why.constraints_applied)


def test_objective_domain_emphasis_reorders_candidate_pool():
    """Under CNS fatigue, a readiness redirect ("Metabolic Conditioning",
    domain="") is generated alongside the Powerlifting domain pool. With no
    objective_domain, it outranks the "SBD Strength" domain candidate. Setting
    objective_domain="powerlifting" boosts every powerlifting-domain
    candidate by OBJECTIVE_DOMAIN_BOOST, pushing "SBD Strength" back ahead of
    the non-matching redirect — a directly observable score effect."""
    fatigued = _neutral_state(cns=65.0)

    baseline_log: list = []
    recommend_next_session(
        fatigued, goal="Powerlifting", candidate_log_out=baseline_log
    )
    baseline_types = [c.type for c in baseline_log]
    assert "Metabolic Conditioning" in baseline_types
    assert "SBD Strength" in baseline_types
    assert baseline_types.index("Metabolic Conditioning") < baseline_types.index("SBD Strength")

    emphasized_log: list = []
    recommend_next_session(
        fatigued,
        goal="Powerlifting",
        block_context={"objective_domain": "powerlifting"},
        candidate_log_out=emphasized_log,
    )
    emphasized_types = [c.type for c in emphasized_log]
    assert emphasized_types.index("SBD Strength") < emphasized_types.index("Metabolic Conditioning")


# ---------------------------------------------------------------------------
# Regression: no objective signals present/None → unchanged behavior
# ---------------------------------------------------------------------------

def test_no_objective_signals_is_unchanged_baseline():
    rx_absent = recommend_next_session(_neutral_state(), goal="Powerlifting")
    rx_none = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"objective_taper": None, "objective_domain": None},
    )
    assert rx_absent.duration_min == _TEMPLATE_DURATION == rx_none.duration_min
    for rx in (rx_absent, rx_none):
        assert rx.why is not None
        assert not any(c.startswith("objective:") for c in rx.why.constraints_applied)
