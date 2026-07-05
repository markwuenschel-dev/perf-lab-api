"""Risk-aware MPC objective ``J`` over a rolled-forward trajectory (ADR-0042).

    J = w_G·ΔG − λ_F·fatigue − λ_T·tissue − λ_I·injury − λ_D·deload − λ_U·uncertainty

ΔG is goal-weighted capacity gain across the horizon; the penalties are the mean fatigue
and peak tissue load along the trajectory, the tissue-injury risk and deload need at the
horizon end, and the current EKF belief uncertainty tr(P). All terms are normalized to
roughly [0, 1] so the hand-set weights are commensurate. Pure / DB-free — the belief trace
is passed in.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.logic.constraint_engine.candidate import max_tissue_load, mean_fatigue
from app.logic.deload_need import compute_deload_need
from app.logic.domain_vocab import canonical_domain
from app.logic.tissue_risk import compute_tissue_risk
from app.schemas.state import UnifiedStateVector

# Canonical domain → capacity-axis emphasis for goal progress (weights, not normalized).
_GOAL_AXIS_WEIGHTS: dict[str, dict[str, float]] = {
    "strength": {"max_strength": 1.0, "hypertrophy": 0.3, "power": 0.3},
    "powerlifting": {"max_strength": 1.0, "hypertrophy": 0.3, "power": 0.3},
    "hypertrophy": {"hypertrophy": 1.0, "max_strength": 0.3},
    "power": {"power": 1.0, "max_strength": 0.5},
    "weightlifting": {"power": 1.0, "max_strength": 0.6},
    "running": {"aerobic": 1.0, "work_capacity": 0.4},
    "endurance": {"aerobic": 1.0, "work_capacity": 0.4},
    "sprinting": {"power": 0.7, "glycolytic": 0.6, "aerobic": 0.3},
    "general": {"max_strength": 0.4, "aerobic": 0.4, "work_capacity": 0.4, "hypertrophy": 0.3},
}
_DEFAULT_GOAL_AXES = _GOAL_AXIS_WEIGHTS["general"]


@dataclass
class MpcWeights:
    """Hand-set cost weights (priors; offline-calibrated later, Stage 8)."""

    w_goal: float = 12.0           # ΔG is small per-horizon capacity gain — scaled up
    lambda_fatigue: float = 0.50
    lambda_tissue: float = 0.40
    lambda_injury: float = 1.00
    lambda_deload: float = 0.40
    lambda_uncertainty: float = 0.30
    uncertainty_ref: float = 12.0  # tr(P) reference (~block-diagonal seed) → unc_norm∈[0,1]


@dataclass
class ObjectiveBreakdown:
    goal_gain: float          # raw goal-weighted capacity gain (points)
    fatigue: float            # mean-fatigue-along-traj, normalized [0,1]
    tissue: float             # peak-tissue-load-along-traj, normalized [0,1]
    injury: float             # max tissue-injury risk at horizon end [0,1]
    deload: float             # deload-need score at horizon end [0,1]
    uncertainty: float        # tr(P) normalized [0,1]
    J: float
    terms: dict[str, float]  # signed contributions per term


def goal_axis_weights(goal: str) -> dict[str, float]:
    """Capacity-axis emphasis vector for a goal (via its canonical domain)."""
    return _GOAL_AXIS_WEIGHTS.get(canonical_domain(goal), _DEFAULT_GOAL_AXES)


def _capacity_ceiling(key: str) -> float:
    return 650.0 if key == "aerobic" else 100.0


def goal_progress(traj: Sequence[UnifiedStateVector], goal: str) -> float:
    """Goal-weighted, ceiling-normalized capacity gain from first to last state."""
    if len(traj) < 2:
        return 0.0
    weights = goal_axis_weights(goal)
    wsum = sum(weights.values()) or 1.0
    start, end = traj[0].capacity_x, traj[-1].capacity_x
    gain = 0.0
    for axis, w in weights.items():
        delta = float(getattr(end, axis)) - float(getattr(start, axis))
        gain += w * (delta / _capacity_ceiling(axis))
    return gain / wsum


def score_trajectory(
    traj: Sequence[UnifiedStateVector],
    goal: str,
    belief_trace: float,
    weights: MpcWeights | None = None,
) -> ObjectiveBreakdown:
    """Score a rolled-forward trajectory with the risk-aware objective J."""
    w = weights or MpcWeights()
    final = traj[-1]

    goal_gain = goal_progress(traj, goal)
    # Convex (squared) fatigue/tissue penalties: the same added load is cheap when fresh
    # and expensive when already loaded — so a fresh athlete trains hard, a loaded one backs
    # off, rather than the planner trivially always preferring the lightest option.
    fatigue = sum((mean_fatigue(s) / 100.0) ** 2 for s in traj) / len(traj)
    tissue = max((max_tissue_load(s) / 100.0) ** 2 for s in traj)
    risk = compute_tissue_risk(final).risk_by_axis
    injury = max(risk.values()) if risk else 0.0
    deload = compute_deload_need(final).score
    uncertainty = max(0.0, min(1.0, belief_trace / max(1e-9, w.uncertainty_ref)))

    terms: dict[str, float] = {
        "goal": w.w_goal * goal_gain,
        "fatigue": -w.lambda_fatigue * fatigue,
        "tissue": -w.lambda_tissue * tissue,
        "injury": -w.lambda_injury * injury,
        "deload": -w.lambda_deload * deload,
        "uncertainty": -w.lambda_uncertainty * uncertainty,
    }
    return ObjectiveBreakdown(
        goal_gain=goal_gain, fatigue=fatigue, tissue=tissue, injury=injury,
        deload=deload, uncertainty=uncertainty, J=sum(terms.values()), terms=terms,
    )
