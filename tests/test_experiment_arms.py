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
    """Static arm must not use adaptive score optimization — only safety substitutions.

    Structural oracle: the static arm fills candidate_log_out with goal_candidates
    in INSERTION ORDER, while the adaptive arm fills it with the score-SORTED list.
    For the default Strength state the insertion order differs from score order at
    index 1 (Skill Acquisition vs Strength Volume), so the two logs are NOT equal.

    If the static arm were changed to route through ``scored``/``_score_with_context``
    its log would be score-sorted and the assertion `static_scores != sorted(...)` would
    fail — making the test a real, falsifiable guard.
    """
    from app.logic.constraint_engine.candidate import score_candidate

    state = _state()
    collected_adaptive: list[SessionCandidate] = []
    collected_static: list[SessionCandidate] = []
    rx_adaptive = recommend_next_session(state, prescription_arm="adaptive", candidate_log_out=collected_adaptive)
    rx_static = recommend_next_session(state, prescription_arm="static_with_safety_caps", candidate_log_out=collected_static)

    # Both arms must return a prescription.
    assert rx_adaptive is not None
    assert rx_static is not None

    # Annotation must be present — unconditionally, NOT guarded by `if rx_static.why`.
    assert rx_static.why is not None, "Static arm must always produce an explanation"
    applied = rx_static.why.constraints_applied
    assert any("static_with_safety_caps" in c for c in applied), (
        f"Static arm must annotate decision mode. Got: {applied}"
    )

    # Structural proof: adaptive log is score-sorted (descending); static log is NOT.
    # This directly catches any regression where the static arm routes through `scored`.
    assert len(collected_static) >= 2, "Need at least 2 candidates for ordering check"
    assert len(collected_adaptive) >= 2, "Need at least 2 candidates for ordering check"

    adaptive_scores = [score_candidate(c) for c in collected_adaptive]
    static_scores = [score_candidate(c) for c in collected_static]

    assert adaptive_scores == sorted(adaptive_scores, reverse=True), (
        "Adaptive arm log must be in score-descending order"
    )
    assert static_scores != sorted(static_scores, reverse=True), (
        "Static arm log is score-sorted — arm leaked through _score_with_context; "
        "it must use insertion-ordered goal_candidates instead"
    )


def test_experiment_assignment_model():
    from app.models.experiment import ExperimentAssignment
    ea = ExperimentAssignment(
        user_id=1, experiment_name="adaptive_vs_static_v1",
        arm="static_with_safety_caps",
    )
    assert ea.arm == "static_with_safety_caps"
    assert ea.active is True
