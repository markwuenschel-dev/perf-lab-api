from __future__ import annotations

from datetime import UTC, datetime

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.interference import (
    directional_interference_multiplier,
    suppression_exp,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(metabolic: float = 0.0, structural: float = 0.0, cns: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(metabolic=metabolic, structural=structural, cns=cns)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_suppression_exp_at_zero_is_one():
    assert abs(suppression_exp(0.0, alpha=1.0, floor=0.3) - 1.0) < 1e-9


def test_suppression_exp_bounded_by_floor():
    for alpha in (0.5, 1.0, 2.0):
        val = suppression_exp(100.0, alpha=alpha, floor=0.30)
        assert val >= 0.30, f"alpha={alpha}: {val} below floor"


def test_suppression_exp_monotonically_decreasing():
    for z1, z2 in [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]:
        assert suppression_exp(z1, alpha=1.0) > suppression_exp(z2, alpha=1.0)


def test_higher_endurance_load_reduces_strength_multiplier():
    p = default_parameters()
    s_low = _state(metabolic=10.0, structural=10.0)
    s_high = _state(metabolic=70.0, structural=70.0)
    m_low = directional_interference_multiplier("max_strength", s_low, p)
    m_high = directional_interference_multiplier("max_strength", s_high, p)
    assert m_low > m_high, f"Low endurance ({m_low:.3f}) should be less suppressed than high ({m_high:.3f})"


def test_strength_multiplier_bounded():
    p = default_parameters()
    s = _state(metabolic=100.0, structural=100.0)
    m = directional_interference_multiplier("max_strength", s, p)
    floor = p.interference_floor_by_axis.get("max_strength", 0.30)
    assert m >= floor
    assert m <= 1.0


def test_power_suppressed_by_cns_fatigue():
    p = default_parameters()
    s_low_cns = _state(cns=10.0)
    s_high_cns = _state(cns=80.0)
    m_low = directional_interference_multiplier("power", s_low_cns, p)
    m_high = directional_interference_multiplier("power", s_high_cns, p)
    assert m_low > m_high


def test_aerobic_not_over_suppressed_by_low_structural():
    p = default_parameters()
    s = _state(structural=20.0)
    m = directional_interference_multiplier("aerobic", s, p)
    assert m >= 0.80, f"Low structural fatigue should barely suppress aerobic, got {m:.3f}"


def test_skill_more_cns_sensitive_than_aerobic():
    p = default_parameters()
    s = _state(cns=60.0)
    m_skill = directional_interference_multiplier("skill", s, p)
    m_aerobic = directional_interference_multiplier("aerobic", s, p)
    assert m_skill < m_aerobic, "Skill should be more CNS-sensitive than aerobic"


def test_work_capacity_has_no_suppression():
    p = default_parameters()
    s = _state(metabolic=90.0, structural=90.0, cns=90.0)
    m = directional_interference_multiplier("work_capacity", s, p)
    assert m == 1.0, "work_capacity has no interference suppression"
