"""
Core candidate model and scoring for the prescription system.

This module owns the representation of "possible next sessions" and
how they are ranked. It is intentionally decoupled from both the
candidate *generators* (in prescriber.py) and the template *validators*
(in validator.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


@dataclass
class SessionCandidate:
    """
    A possible next training session under consideration.

    This is the central data structure for the candidate-based prescriber.
    It carries both descriptive information and the raw scoring signals.
    """
    type: str
    focus: str
    rationale: str
    duration_min: int
    branch_id: str

    # Scoring axes (0–1 each, higher = better)
    goal_alignment: float = 1.0
    state_fit: float = 1.0
    fatigue_penalty: float = 0.0      # high value = worse
    tissue_penalty: float = 0.0       # high value = worse
    novelty_bonus: float = 0.0
    habit_bonus: float = 0.0
    template_bias: float = 0.0

    # Weak-point coverage: fraction of active weak-point tags addressed
    weak_point_coverage: float = 0.0

    # Hard safety override — these bypass normal scoring
    is_safety_override: bool = False

    # Optional provenance
    source: str = "generator"  # generator | redirect | safety | template


# Default scoring weights (can be overridden per use case)
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "goal_alignment": 0.30,
    "state_fit": 0.25,
    "weak_point_coverage": 0.15,
    "fatigue_penalty": -0.15,
    "tissue_penalty": -0.08,
    "novelty_bonus": 0.04,
    "habit_bonus": 0.03,
    "template_bias": 0.05,
}


def score_candidate(
    candidate: SessionCandidate,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute a linear weighted score in [0, 1]."""
    w = weights or DEFAULT_SCORE_WEIGHTS
    total = 0.0
    for axis, weight in w.items():
        val = getattr(candidate, axis, 0.0)
        total += weight * val
    return max(0.0, min(1.0, total))


def apply_block_context_boost(
    candidate: SessionCandidate,
    block_context: dict | None,
    boost: float = 0.15,
) -> float:
    """Helper used by prescriber to apply block-level bias."""
    if not block_context:
        return 0.0
    if block_context.get("session_category") and candidate.type == block_context["session_category"]:
        return boost
    return 0.0


# Small readiness helpers that are useful across generators and scorers
def mean_fatigue(state: UnifiedStateVector) -> float:
    f = state.fatigue_f
    values = [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    return sum(values) / len(values)


def max_tissue_load(state: UnifiedStateVector) -> float:
    t = state.tissue_t
    return max(
        t.shoulder, t.elbow, t.wrist, t.lumbar,
        t.hip, t.knee, t.ankle, t.finger,
    )


def overall_readiness(state: UnifiedStateVector) -> float:
    """0–1 readiness (1 = fully fresh)."""
    mf = mean_fatigue(state)
    return max(0.0, 1.0 - mf / 100.0)
