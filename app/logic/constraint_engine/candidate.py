"""
Core candidate model and scoring for the prescription system.

This module owns the representation of "possible next sessions" and
how they are ranked. It is intentionally decoupled from both the
candidate *generators* (in prescriber.py) and the template *validators*
(in validator.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.state import UnifiedStateVector


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

# ---------------------------------------------------------------------------
# Scoring weight safety constraints (Task 8)
# ---------------------------------------------------------------------------
# fatigue_penalty and tissue_penalty must remain negative (they penalise risky
# sessions). novelty_bonus and habit_bonus are bounded to prevent gaming.
_WEIGHT_CONSTRAINTS: dict[str, dict[str, float]] = {
    "fatigue_penalty": {"max": -0.05},
    "tissue_penalty":  {"max": -0.02},
    "novelty_bonus":   {"min": 0.0, "max": 0.10},
    "habit_bonus":     {"min": 0.0, "max": 0.10},
}


from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass
class ScoreWeightProfile:
    """Versioned container for a scoring weight dict."""

    weights: dict[str, float]
    version: str = "v1"


def validate_score_weights(weights: dict[str, float]) -> list[str]:
    """Return a list of violation messages; empty list means the weights are safe.

    Safety constraints:
    - ``fatigue_penalty`` and ``tissue_penalty`` must remain negative so they
      cannot be zeroed out or inverted by a learned optimiser.
    - ``novelty_bonus`` and ``habit_bonus`` are capped to prevent them from
      overwhelming the readiness-based penalties.
    """
    violations: list[str] = []
    fp = weights.get("fatigue_penalty", -0.15)
    if fp > _WEIGHT_CONSTRAINTS["fatigue_penalty"]["max"]:
        violations.append(
            f"fatigue_penalty={fp:.3f} must be <= "
            f"{_WEIGHT_CONSTRAINTS['fatigue_penalty']['max']} (safety minimum)"
        )
    tp = weights.get("tissue_penalty", -0.08)
    if tp > _WEIGHT_CONSTRAINTS["tissue_penalty"]["max"]:
        violations.append(
            f"tissue_penalty={tp:.3f} must be <= "
            f"{_WEIGHT_CONSTRAINTS['tissue_penalty']['max']} (safety minimum)"
        )
    nb = weights.get("novelty_bonus", 0.0)
    if nb > _WEIGHT_CONSTRAINTS["novelty_bonus"]["max"]:
        violations.append(
            f"novelty_bonus={nb:.3f} must be <= "
            f"{_WEIGHT_CONSTRAINTS['novelty_bonus']['max']}"
        )
    hb = weights.get("habit_bonus", 0.0)
    if hb > _WEIGHT_CONSTRAINTS["habit_bonus"]["max"]:
        violations.append(
            f"habit_bonus={hb:.3f} must be <= "
            f"{_WEIGHT_CONSTRAINTS['habit_bonus']['max']}"
        )
    return violations


def simple_safe_goal_aligned_policy(
    candidates: list[SessionCandidate],
    state: UnifiedStateVector,
    fatigue_limit: float = 60.0,
    tissue_limit: float = 60.0,
) -> SessionCandidate | None:
    """Baseline comparator policy: safety-filtered, goal-aligned scoring.

    Used to establish a Q8 baseline.  Does NOT use learned weights.

    Filtering rules:
    - Safety-override candidates are excluded (they are hard-stop sessions
      that bypass normal scoring entirely).
    - Candidates whose ``fatigue_penalty`` or ``tissue_penalty`` (expressed in
      [0, 1] penalty space) exceed the corresponding limit threshold are
      excluded.  Limits are supplied in [0, 100] state-score space and
      converted to [0, 1] penalty space by dividing by 100.

    The surviving candidate with the highest composite goal-alignment score
    is returned, or ``None`` if no safe candidates remain.
    """
    fat_thresh = fatigue_limit / 100.0
    tis_thresh = tissue_limit / 100.0
    safe = [
        c for c in candidates
        if not c.is_safety_override
        and c.fatigue_penalty < fat_thresh
        and c.tissue_penalty < tis_thresh
    ]
    if not safe:
        return None
    return max(
        safe,
        key=lambda c: c.goal_alignment + 0.5 * c.state_fit + 0.5 * c.weak_point_coverage,
    )


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
    block_context: dict[str, Any] | None,
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
