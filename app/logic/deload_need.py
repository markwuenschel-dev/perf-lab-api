"""Deload need assessment. Shadow mode only (Level 1: explanation).

Implements a rule-guarded baseline using fatigue state, tissue state, and
optional trend signals. Hard rules require one high-fatigue condition.
Soft rules require at least two concurrent signals. No HMM or RL.

Do not use this module to hard-block training. Use existing planning.deload_triggered()
for hard fallback. This module is the precursor to a learned model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

_HARD_FATIGUE_THRESHOLD = 60.0
_HARD_MEAN_FATIGUE_THRESHOLD = 45.0
_HARD_TISSUE_THRESHOLD = 55.0
_SOFT_PERF_SLOPE_THRESHOLD = -0.04    # negative = declining
_SOFT_FATIGUE_SLOPE_THRESHOLD = 0.03  # positive = accumulating
_SOFT_TISSUE_SLOPE_THRESHOLD = 0.02
_SOFT_TISSUE_VALUE_THRESHOLD = 45.0
_SOFT_ADHERENCE_THRESHOLD = 0.70


def _tier_from_score(score: float) -> Literal["none", "watch", "bias", "force"]:
    if score >= 0.75:
        return "force"
    if score >= 0.55:
        return "bias"
    if score >= 0.35:
        return "watch"
    return "none"


def compute_deload_need(
    state: UnifiedStateVector,
    performance_residual_slope: float | None = None,
    mean_fatigue_slope: float | None = None,
    max_tissue_slope: float | None = None,
    recent_adherence: float | None = None,
) -> DeloadNeed:
    """Compute deload need from state and optional trend signals.

    Hard rule: any single fatigue axis > 60, mean fatigue > 45, or any tissue > 55.
    Soft rule: at least two of four trend signals.
    Shadow only — never hard-blocks training.
    """
    f = state.fatigue_f
    t = state.tissue_t

    fatigue_vals = [getattr(f, k) for k in FatigueState.KEYS]
    tissue_vals = [getattr(t, k) for k in TissueState.KEYS]
    mean_f = sum(fatigue_vals) / len(fatigue_vals)
    max_tissue = max(tissue_vals)

    drivers: list[str] = []
    score = 0.0

    # Hard rule check
    hard_fatigue_axis = next((k for k in FatigueState.KEYS if getattr(f, k) > _HARD_FATIGUE_THRESHOLD), None)
    hard_tissue_axis = next((k for k in TissueState.KEYS if getattr(t, k) > _HARD_TISSUE_THRESHOLD), None)
    hard_mean = mean_f > _HARD_MEAN_FATIGUE_THRESHOLD

    hard_rule = hard_fatigue_axis is not None or hard_tissue_axis is not None or hard_mean

    if hard_fatigue_axis:
        v = getattr(f, hard_fatigue_axis)
        drivers.append(f"fatigue_{hard_fatigue_axis}={v:.0f}")
        score += 0.50 + min(0.30, (v - _HARD_FATIGUE_THRESHOLD) / 100.0)

    if hard_tissue_axis:
        v = getattr(t, hard_tissue_axis)
        drivers.append(f"tissue_{hard_tissue_axis}={v:.0f}")
        score += 0.40 + min(0.25, (v - _HARD_TISSUE_THRESHOLD) / 100.0)

    if hard_mean and not hard_fatigue_axis:
        drivers.append(f"mean_fatigue={mean_f:.0f}")
        score += 0.35

    # Soft signal count — require at least two to contribute
    soft_signals = [
        performance_residual_slope is not None and performance_residual_slope < _SOFT_PERF_SLOPE_THRESHOLD,
        mean_fatigue_slope is not None and mean_fatigue_slope > _SOFT_FATIGUE_SLOPE_THRESHOLD,
        (max_tissue_slope is not None and max_tissue_slope > _SOFT_TISSUE_SLOPE_THRESHOLD)
        or max_tissue > _SOFT_TISSUE_VALUE_THRESHOLD,
        recent_adherence is not None and recent_adherence < _SOFT_ADHERENCE_THRESHOLD,
    ]
    n_soft = sum(soft_signals)

    if n_soft >= 2:
        score += 0.20 * n_soft
        if performance_residual_slope is not None and performance_residual_slope < _SOFT_PERF_SLOPE_THRESHOLD:
            drivers.append(f"perf_slope={performance_residual_slope:.3f}")
        if mean_fatigue_slope is not None and mean_fatigue_slope > _SOFT_FATIGUE_SLOPE_THRESHOLD:
            drivers.append(f"fatigue_slope={mean_fatigue_slope:.3f}")
        if max_tissue > _SOFT_TISSUE_VALUE_THRESHOLD:
            drivers.append(f"max_tissue={max_tissue:.0f}")
        if recent_adherence is not None and recent_adherence < _SOFT_ADHERENCE_THRESHOLD:
            drivers.append(f"adherence={recent_adherence:.2f}")

    score = min(1.0, max(0.0, score))
    if hard_rule and score < 0.75:
        score = max(score, 0.55)  # hard rule floors at "bias"

    return DeloadNeed(
        score=score,
        tier=_tier_from_score(score),
        drivers=drivers,
    )


@dataclass
class DeloadNeed:
    score: float
    tier: Literal["none", "watch", "bias", "force"]
    drivers: list[str] = field(default_factory=lambda: [])
    model_version: str = "rule_v1"
    shadow_only: bool = True
