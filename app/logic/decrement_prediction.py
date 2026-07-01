"""Next-session decrement prediction. Shadow mode only (Level 0: log).

Target: observed_next_performance - expected_next_performance_given_plan.
Do NOT use raw next-session performance as the prediction target — that conflates
plan difficulty changes with genuine decrements.

This module is a scaffolding for Q1 (session-pair decrement dataset).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.engine_vectors import FatigueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose


@dataclass
class DecrementPrediction:
    score: float
    affected_axes: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    shadow_only: bool = True


def compute_decrement_prediction(
    prev_dose: StressDose,
    current_state: UnifiedStateVector,
    planned_next_difficulty: float = 0.5,
    time_gap_hours: float = 48.0,
) -> DecrementPrediction:
    """Estimate likelihood of next-session performance decrement.

    Target is a residual: observed_next_performance − expected_next_performance_given_plan
    (see module docstring). Uses previous session dose, current fatigue state, and planned
    next-session difficulty as features.
    Initial implementation: rule-based linear composite. No learned weights yet.
    """
    drivers: list[str] = []
    affected: list[str] = []
    f = current_state.fatigue_f

    six = prev_dose.dose_six
    total_dose = six.volume + six.intensity + six.density + six.impact + six.skill + six.metabolic

    # CNS fatigue: affects neural readiness for high-intensity work
    cns = f.cns
    if cns > 40.0:
        drivers.append(f"cns_fatigue={cns:.0f}")
        affected.append("cns")

    # Muscular fatigue: affects volume tolerance
    muscular = f.muscular
    if muscular > 35.0 and six.volume > 0.5:
        drivers.append(f"muscular_fatigue={muscular:.0f}")
        affected.append("muscular")

    # Short recovery window
    if time_gap_hours < 24.0:
        drivers.append(f"short_gap={time_gap_hours:.0f}h")

    # High previous dose
    if total_dose > 3.5:
        drivers.append(f"high_prev_dose={total_dose:.2f}")

    # Planned difficulty vs current fatigue
    mean_fatigue = sum(getattr(f, k) for k in FatigueState.KEYS) / len(FatigueState.KEYS)
    if planned_next_difficulty > 0.7 and mean_fatigue > 30.0:
        drivers.append(f"high_load_on_fatigue={mean_fatigue:.0f}")

    # Clamp caller-supplied float defensively before weighting.
    pnd = min(1.0, max(0.0, planned_next_difficulty))

    # Weights sum to 1.0: 0.25+0.20+0.20+0.15+0.10+0.10 = 1.00
    score = min(1.0, max(0.0, sum([
        cns / 100.0 * 0.25,
        muscular / 100.0 * 0.20,
        max(0.0, 1.0 - time_gap_hours / 48.0) * 0.20,
        min(1.0, total_dose / 5.0) * 0.15,
        mean_fatigue / 100.0 * 0.10,
        pnd * 0.10,
    ])))

    return DecrementPrediction(
        score=score,
        affected_axes=list(set(affected)),
        drivers=drivers,
    )
