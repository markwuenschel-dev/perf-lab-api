"""
Tests for Task 8: Candidate Scoring Guardrails and Full Logging.

Covers:
- DEFAULT_SCORE_WEIGHTS has negative fatigue / tissue penalties
- validate_score_weights accepts defaults and rejects unsafe values
- simple_safe_goal_aligned_policy selects highest goal-aligned safe candidate
- candidate_log_out on recommend_next_session collects all scored candidates
- ScoreWeightProfile carries a version string
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.constraint_engine.candidate import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeightProfile,
    SessionCandidate,
    simple_safe_goal_aligned_policy,
    validate_score_weights,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(mean_fatigue: float = 10.0, tissue: float = 5.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(
        cns=mean_fatigue,
        muscular=mean_fatigue,
        metabolic=mean_fatigue,
        structural=mean_fatigue,
        tendon=mean_fatigue,
        grip=mean_fatigue,
    )
    t = TissueState(knee=tissue, lumbar=tissue)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.0,
        skill_state={},
        **leg,
    )


def _candidate(
    goal_alignment: float = 0.8,
    state_fit: float = 0.8,
    fatigue_penalty: float = 0.1,
    tissue_penalty: float = 0.05,
    weak_point_coverage: float = 0.3,
    type: str = "Max Strength",
    branch_id: str = "strength_heavy",
) -> SessionCandidate:
    return SessionCandidate(
        type=type,
        focus="Squat 5x3",
        rationale="test",
        duration_min=60,
        branch_id=branch_id,
        goal_alignment=goal_alignment,
        state_fit=state_fit,
        fatigue_penalty=fatigue_penalty,
        tissue_penalty=tissue_penalty,
        weak_point_coverage=weak_point_coverage,
    )


# ---------------------------------------------------------------------------
# DEFAULT_SCORE_WEIGHTS safety invariants
# ---------------------------------------------------------------------------

def test_default_weights_have_negative_fatigue_and_tissue():
    assert DEFAULT_SCORE_WEIGHTS["fatigue_penalty"] < 0
    assert DEFAULT_SCORE_WEIGHTS["tissue_penalty"] < 0


def test_validate_accepts_default_weights():
    violations = validate_score_weights(DEFAULT_SCORE_WEIGHTS)
    assert violations == [], f"Default weights should be valid: {violations}"


# ---------------------------------------------------------------------------
# validate_score_weights rejects unsafe values
# ---------------------------------------------------------------------------

def test_validate_rejects_zero_fatigue_penalty():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "fatigue_penalty": 0.0}
    violations = validate_score_weights(bad_weights)
    assert any("fatigue_penalty" in v for v in violations)


def test_validate_rejects_positive_fatigue_penalty():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "fatigue_penalty": 0.10}
    violations = validate_score_weights(bad_weights)
    assert violations


def test_validate_rejects_high_novelty_bonus():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "novelty_bonus": 0.50}
    violations = validate_score_weights(bad_weights)
    assert any("novelty_bonus" in v for v in violations)


# ---------------------------------------------------------------------------
# simple_safe_goal_aligned_policy
# ---------------------------------------------------------------------------

def test_simple_policy_returns_highest_goal_alignment():
    candidates = [
        _candidate(goal_alignment=0.9, branch_id="a"),
        _candidate(goal_alignment=0.5, branch_id="b"),
        _candidate(goal_alignment=0.7, branch_id="c"),
    ]
    s = _state()
    winner = simple_safe_goal_aligned_policy(candidates, s)
    assert winner is not None
    assert winner.branch_id == "a"


def test_simple_policy_filters_high_fatigue():
    safe = _candidate(fatigue_penalty=0.20, branch_id="safe")
    risky = _candidate(fatigue_penalty=0.90, goal_alignment=1.0, branch_id="risky")
    s = _state(mean_fatigue=10.0)
    winner = simple_safe_goal_aligned_policy([safe, risky], s, fatigue_limit=60.0)
    assert winner is not None
    assert winner.branch_id == "safe"


# ---------------------------------------------------------------------------
# candidate_log_out on recommend_next_session
# ---------------------------------------------------------------------------

def test_candidate_log_out_collects_all_candidates():
    from app.logic.prescriber import recommend_next_session

    s = _state()
    collected: list = []
    _ = recommend_next_session(s, candidate_log_out=collected)
    assert len(collected) > 0, "candidate_log_out must be populated"


# ---------------------------------------------------------------------------
# ScoreWeightProfile versioning
# ---------------------------------------------------------------------------

def test_score_weight_profile_versioned():
    profile = ScoreWeightProfile(weights=DEFAULT_SCORE_WEIGHTS, version="v1")
    assert profile.version == "v1"
