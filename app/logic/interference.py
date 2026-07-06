"""Concurrent-training interference suppression (ADR-0037).

Replaces the inline linear _interference_factor calls in state_update_v0
with an explicit, testable, smooth exponential suppression formula.

Default behavior remains close to the prior linear model but avoids
the discontinuity at z=0 and the hard floor artifact.
"""
from __future__ import annotations

import math

from app.engine.parameters import EngineParameters
from app.schemas.state import UnifiedStateVector


def suppression_exp(z: float, alpha: float, floor: float = 0.30) -> float:
    """Exponential interference suppression in [floor, 1.0].

    z = interfering load fraction [0, 1+].
    alpha = sharpness of suppression.
    floor = minimum adaptation efficiency under maximal interference.

    z=0   → 1.0 (no suppression)
    z→∞  → floor (maximum suppression)
    Monotonically decreasing and bounded.
    """
    z = max(0.0, z)
    return floor + (1.0 - floor) * math.exp(-alpha * z)


def _endurance_load_fraction(state: UnifiedStateVector) -> float:
    """Proxy for concurrent endurance load as a [0, 1] fraction."""
    f = state.fatigue_f
    endurance_load = 0.4 * f.metabolic + 0.6 * f.structural
    return endurance_load / 100.0


def _concurrent_endurance_excess(state: UnifiedStateVector, params: EngineParameters) -> float:
    """Off-axis endurance load *beyond* the baseline a hard strength block itself
    produces (ADR-0037 recalibration).

    A strength block generates structural/metabolic fatigue that the raw endurance-load
    proxy would read as interference — causing the block to suppress its own adaptation.
    Subtracting ``interference_baseline_z0`` keys strength/hypertrophy interference on the
    *added* concurrent-endurance load, so a strength block no longer self-penalizes and
    only genuine concurrent conditioning blunts the gain.
    """
    return max(0.0, _endurance_load_fraction(state) - params.interference_baseline_z0)


def directional_interference_multiplier(
    target_axis: str,
    state: UnifiedStateVector,
    params: EngineParameters,
) -> float:
    """Adaptation efficiency multiplier ∈ [floor, 1.0] for target_axis.

    Returns 1.0 (no suppression) for axes without interference rules.
    Never returns a value that increases adaptation above 1.0.
    """
    f = state.fatigue_f
    floor = params.interference_floor_by_axis.get(target_axis, 0.30)

    if target_axis == "max_strength":
        z = _concurrent_endurance_excess(state, params)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "power":
        # Two suppression channels combined via min() (weakest-link model):
        #   1. Endurance-load proxy (0.4·metabolic + 0.6·structural), the same
        #      channel structural fatigue always entered power through.
        #   2. CNS fatigue — deliberate modeling change from the legacy
        #      INTERFERENCE_DAM_ON_POWER structural-product; CNS is now the
        #      second limiter rather than a separate structural multiplier.
        z_e = _endurance_load_fraction(state)
        z_cns = f.cns / 100.0
        m_e = suppression_exp(z_e, params.interference_e_on_power_alpha, floor)
        m_cns = suppression_exp(z_cns, params.interference_cns_on_power_alpha, floor)
        return min(m_e, m_cns)

    if target_axis == "hypertrophy":
        z = _concurrent_endurance_excess(state, params)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "skill":
        z_cns = f.cns / 100.0
        return suppression_exp(z_cns, params.interference_cns_on_skill_alpha, floor)

    if target_axis == "aerobic":
        # Structural fatigue slightly suppresses aerobic quality work
        z = f.structural / 100.0
        return suppression_exp(z, params.interference_structural_on_endurance_quality_alpha, floor)

    return 1.0  # glycolytic, mobility, work_capacity: no interference suppression
