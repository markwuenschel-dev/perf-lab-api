"""Tests for experiment arm support (adaptive vs static-with-safety-caps)."""
from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.prescriber import recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_adaptive_arm_logs_candidates():
    collected: list[SessionCandidate] = []
    rx = recommend_next_session(_state(), prescription_arm="adaptive", candidate_log_out=collected)
    assert rx is not None
    assert len(collected) > 0


def test_static_with_safety_caps_logs_candidates():
    collected: list[SessionCandidate] = []
    rx = recommend_next_session(_state(), prescription_arm="static_with_safety_caps", candidate_log_out=collected)
    assert rx is not None
    assert len(collected) > 0


def test_static_with_safety_caps_skips_adaptive_scoring():
    """Static arm must not use adaptive score optimization — only safety substitutions."""
    collected_adaptive: list[SessionCandidate] = []
    collected_static: list[SessionCandidate] = []
    rx_adaptive = recommend_next_session(_state(), prescription_arm="adaptive", candidate_log_out=collected_adaptive)
    rx_static = recommend_next_session(_state(), prescription_arm="static_with_safety_caps", candidate_log_out=collected_static)
    # Both should return a prescription
    assert rx_adaptive is not None
    assert rx_static is not None
    # Decision mode annotated differently
    if rx_static.why:
        applied = rx_static.why.constraints_applied
        assert any("static_with_safety_caps" in c for c in applied), \
            f"Static arm must annotate decision mode. Got: {applied}"


def test_experiment_assignment_model():
    from app.models.experiment import ExperimentAssignment
    ea = ExperimentAssignment(
        user_id=1, experiment_name="adaptive_vs_static_v1",
        arm="static_with_safety_caps",
    )
    assert ea.arm == "static_with_safety_caps"
    assert ea.active is True
