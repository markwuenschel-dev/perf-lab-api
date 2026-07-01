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
    assert any("fatigue_penalty" in v for v in violations)


def test_validate_rejects_insufficient_tissue_penalty():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "tissue_penalty": 0.0}
    violations = validate_score_weights(bad_weights)
    assert any("tissue_penalty" in v for v in violations)


def test_validate_rejects_high_habit_bonus():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "habit_bonus": 0.50}
    violations = validate_score_weights(bad_weights)
    assert any("habit_bonus" in v for v in violations)


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


def test_simple_policy_uses_pure_goal_alignment_not_composite():
    """Prove the policy selects on goal_alignment alone, not a composite.

    Both candidates are safe.  The composite (goal_alignment + 0.5*state_fit +
    0.5*weak_point_coverage) would select "composite_winner" (0.7 + 0.45 + 0.45 = 1.60)
    over "goal_winner" (0.8 + 0.0 + 0.0 = 0.80).  Pure goal_alignment selects
    "goal_winner" (0.8 > 0.7).  If the composite were still in use this test fails.
    """
    goal_winner = _candidate(
        goal_alignment=0.8,
        state_fit=0.0,
        weak_point_coverage=0.0,
        branch_id="goal_winner",
    )
    composite_winner = _candidate(
        goal_alignment=0.7,
        state_fit=0.9,
        weak_point_coverage=0.9,
        branch_id="composite_winner",
    )
    s = _state()
    winner = simple_safe_goal_aligned_policy([goal_winner, composite_winner], s)
    assert winner is not None
    assert winner.branch_id == "goal_winner"


def test_simple_policy_excludes_safety_override():
    """A safety-override candidate must never be selected, even with high goal_alignment."""
    override = SessionCandidate(
        type="Recovery",
        focus="Rest",
        rationale="hard stop",
        duration_min=20,
        branch_id="override",
        goal_alignment=1.0,
        fatigue_penalty=0.0,
        tissue_penalty=0.0,
        is_safety_override=True,
    )
    normal = _candidate(goal_alignment=0.5, branch_id="normal")
    s = _state()
    winner = simple_safe_goal_aligned_policy([override, normal], s)
    assert winner is not None
    assert winner.branch_id == "normal"


def test_simple_policy_returns_none_when_all_filtered():
    """Returns None when every candidate is either a safety override or exceeds limits."""
    override = SessionCandidate(
        type="Recovery",
        focus="Rest",
        rationale="hard stop",
        duration_min=20,
        branch_id="override",
        goal_alignment=1.0,
        fatigue_penalty=0.0,
        tissue_penalty=0.0,
        is_safety_override=True,
    )
    high_fatigue = _candidate(fatigue_penalty=0.90, branch_id="high_fatigue")
    s = _state(mean_fatigue=10.0)
    result = simple_safe_goal_aligned_policy([override, high_fatigue], s, fatigue_limit=60.0)
    assert result is None


# ---------------------------------------------------------------------------
# candidate_log_out on recommend_next_session
# ---------------------------------------------------------------------------

def test_candidate_log_out_collects_all_candidates():
    """Prove the full scored pool is captured, not just the winner.

    The chosen prescription must match collected[0] (the highest-scored
    candidate), and len(collected) > 1 proves that rejected candidates are
    also present — if only the winner were logged the second assertion fails.
    """
    from app.logic.prescriber import recommend_next_session

    s = _state()
    collected: list = []
    rx = recommend_next_session(s, candidate_log_out=collected)
    assert len(collected) > 1, (
        "candidate_log_out must contain ALL scored candidates, not just the winner"
    )
    assert rx.type == collected[0].type, (
        "The returned prescription's type must match the top-scored logged candidate"
    )


# ---------------------------------------------------------------------------
# ScoreWeightProfile versioning
# ---------------------------------------------------------------------------

def test_score_weight_profile_versioned():
    profile = ScoreWeightProfile(weights=DEFAULT_SCORE_WEIGHTS, version="v1")
    assert profile.version == "v1"
