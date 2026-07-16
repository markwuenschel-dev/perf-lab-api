"""Tissue risk assessment from lagged cumulative exposure. Shadow mode only (Level 0: log).

Uses exponentially-weighted lagged exposure features. Tissue risk is derived
exclusively from explicit exposure data and current tissue state — never from
inferred event labels of any kind.

This model reports ``calibrated=False`` and runs shadow/offline only: its output feeds the MPC
shadow objective (``app/logic/mpc/objective.py``) and offline training (``app/ml/q3_tissue``),
never the live prescription-scoring path. An uncalibrated model must not become live candidate
authority; promoting it to a soft penalty is a future feature mission that must calibrate and
wire a per-candidate producer first — there is deliberately no runtime flag until that live
path exists. (The separate ``tissue_t`` arithmetic in ``candidate_library`` is a different,
pre-existing live mechanism, not this model — see test_tissue_risk_model_not_live_wired.py.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.engine_vectors import TissueState
from app.schemas.state import UnifiedStateVector

_AMBER_THRESHOLD = 0.30
_RED_THRESHOLD = 0.60


def _tier(risk: float) -> Literal["green", "amber", "red"]:
    if risk >= _RED_THRESHOLD:
        return "red"
    if risk >= _AMBER_THRESHOLD:
        return "amber"
    return "green"


@dataclass
class TissueRiskPrediction:
    risk_by_axis: dict[str, float]
    delta_risk_by_axis: dict[str, float]
    tier_by_axis: dict[str, Literal["green", "amber", "red"]]
    drivers: dict[str, list[str]] = field(default_factory=lambda: {})
    calibrated: bool = False
    shadow_only: bool = True


def compute_tissue_risk(
    state: UnifiedStateVector,
    lagged_exposure_3d: dict[str, float] | None = None,
    lagged_exposure_7d: dict[str, float] | None = None,
    lagged_exposure_28d: dict[str, float] | None = None,
    prior_pain_axes: set[str] | None = None,
) -> TissueRiskPrediction:
    """Estimate tissue risk per axis from state and lagged exposure features.

    Lagged exposures are cumulative dose units (e.g. tissue_impulse * exp(-dt/tau)).
    Risk is derived only from explicit exposure data and current tissue state.
    """
    pain_axes = prior_pain_axes or set()
    exp_3d = lagged_exposure_3d or {}
    exp_7d = lagged_exposure_7d or {}
    exp_28d = lagged_exposure_28d or {}

    risk_by_axis: dict[str, float] = {}
    drivers: dict[str, list[str]] = {}

    for axis in TissueState.KEYS:
        d3 = exp_3d.get(axis, 0.0)
        d7 = exp_7d.get(axis, 0.0)
        d28 = exp_28d.get(axis, 0.0)

        chronic_weekly = d28 / 4.0 if d28 > 0 else 0.0
        ac_ratio = d7 / max(chronic_weekly, 1e-6) if chronic_weekly > 0 else 1.0

        # State-based base risk (current accumulated tissue stress)
        tissue_val = getattr(state.tissue_t, axis, 0.0)
        base_risk = tissue_val / 100.0 * 0.50

        # Acute:chronic spike (ACWR > 1.3 starts adding risk)
        spike_risk = min(0.30, max(0.0, (ac_ratio - 1.3) / 1.7) * 0.30) if ac_ratio > 1.3 else 0.0

        # Recent concentration (3d exposure relative to 7d)
        concentration = d3 / max(d7, 1e-6) if d7 > 0 else 0.0
        concentration_risk = max(0.0, concentration - 0.5) * 0.10

        # Prior pain at this axis
        pain_bump = 0.15 if axis in pain_axes else 0.0

        risk = min(1.0, base_risk + spike_risk + concentration_risk + pain_bump)
        risk_by_axis[axis] = risk

        axis_drivers: list[str] = []
        if tissue_val > 40.0:
            axis_drivers.append(f"tissue_state={tissue_val:.0f}")
        if ac_ratio > 1.3:
            axis_drivers.append(f"ac_ratio={ac_ratio:.2f}")
        if concentration > 0.5:
            axis_drivers.append(f"concentration={concentration:.2f}")
        if axis in pain_axes:
            axis_drivers.append("prior_pain")
        drivers[axis] = axis_drivers

    delta_risk: dict[str, float] = dict.fromkeys(TissueState.KEYS, 0.0)
    tier_by_axis: dict[str, Literal["green", "amber", "red"]] = {
        k: _tier(v) for k, v in risk_by_axis.items()
    }

    return TissueRiskPrediction(
        risk_by_axis=risk_by_axis,
        delta_risk_by_axis=delta_risk,
        tier_by_axis=tier_by_axis,
        drivers=drivers,
    )
