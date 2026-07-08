"""The wellness→plan score channel (ADR-0052): a bad-night readiness override
transparently nudges candidate scoring, and confidence never enters the prescriber."""
from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.prescriber import _generate_candidates, recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=60.0, hypertrophy=50.0, skill=50.0, mobility=50.0)
    f = FatigueState(cns=20.0, muscular=30.0, grip=10.0)
    t = TissueState(lumbar=8.0, knee=12.0, hip=25.0)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={}, **leg,
    )


def test_override_flows_into_candidate_scoring():
    state = _state()
    high = _generate_candidates(state, "Strength", {}, None, readiness_override=0.95)
    low = _generate_candidates(state, "Strength", {}, None, readiness_override=0.10)
    # Same templates, lower readiness → readiness-responsive candidates score lower state_fit.
    by_branch_high = {c.branch_id: c for c in high}
    responsive = [
        b for b, c in by_branch_high.items()
        if c.state_fit > next(x.state_fit for x in low if x.branch_id == b)
    ]
    assert responsive, "a lower readiness override must reduce state_fit for ≥1 candidate"


def test_override_none_falls_back_to_modeled():
    state = _state()
    default = _generate_candidates(state, "Strength", {}, None)
    explicit = _generate_candidates(state, "Strength", {}, None, readiness_override=None)
    assert [c.state_fit for c in default] == [c.state_fit for c in explicit]


def test_recommend_accepts_override_and_returns_prescription():
    state = _state()
    rx = recommend_next_session(state, goal="Strength", readiness_override=0.1)
    assert rx is not None
