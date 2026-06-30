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
        z = _endurance_load_fraction(state)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "power":
        z_e = _endurance_load_fraction(state)
        z_cns = f.cns / 100.0
        m_e = suppression_exp(z_e, params.interference_e_on_power_alpha, floor)
        m_cns = suppression_exp(z_cns, params.interference_cns_on_power_alpha, floor)
        return min(m_e, m_cns)

    if target_axis == "hypertrophy":
        z = _endurance_load_fraction(state)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "skill":
        z_cns = f.cns / 100.0
        return suppression_exp(z_cns, params.interference_cns_on_skill_alpha, floor)

    if target_axis == "aerobic":
        # Structural fatigue slightly suppresses aerobic quality work
        z = f.structural / 100.0
        return suppression_exp(z, params.interference_structural_on_endurance_quality_alpha, floor)

    return 1.0  # glycolytic, mobility, work_capacity: no interference suppression
