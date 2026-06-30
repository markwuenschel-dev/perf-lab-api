# tests/test_deload_need.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.deload_need import DeloadNeed, compute_deload_need
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(
    cns: float = 0.0, muscular: float = 0.0, metabolic: float = 0.0,
    structural: float = 0.0, tendon: float = 0.0, grip: float = 0.0,
    lumbar: float = 0.0, knee: float = 0.0,
) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular, metabolic=metabolic,
                     structural=structural, tendon=tendon, grip=grip)
    t = TissueState(lumbar=lumbar, knee=knee)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_fresh_state_gives_tier_none():
    s = _state(cns=10.0, muscular=10.0)
    result = compute_deload_need(s)
    assert result.tier == "none"
    assert result.shadow_only is True


def test_single_high_fatigue_axis_gives_watch_or_bias():
    s = _state(cns=65.0)  # one axis over 60
    result = compute_deload_need(s)
    assert result.tier in ("watch", "bias", "force")


def test_hard_rule_any_axis_over_60_triggers_force_or_bias():
    s = _state(cns=75.0)
    result = compute_deload_need(s)
    assert result.score >= 0.55, "Single very high fatigue axis should score bias or force"
    assert "cns" in " ".join(result.drivers).lower() or any("fatigue" in d for d in result.drivers)


def test_two_soft_signals_required_for_bias():
    """Single soft signal (without hard rule) must not produce bias tier."""
    s = _state(cns=30.0, muscular=30.0)  # no hard rule
    result = compute_deload_need(
        s,
        performance_residual_slope=-0.05,  # one soft signal
        mean_fatigue_slope=None,
        max_tissue_slope=None,
        recent_adherence=None,
    )
    assert result.tier in ("none", "watch"), f"Single soft signal should not trigger bias, got {result.tier}"


def test_two_soft_signals_can_reach_bias():
    s = _state(cns=30.0, muscular=30.0)
    result = compute_deload_need(
        s,
        performance_residual_slope=-0.06,   # soft signal 1
        mean_fatigue_slope=0.04,             # soft signal 2
        max_tissue_slope=None,
        recent_adherence=None,
    )
    assert result.tier in ("watch", "bias")


def test_deload_need_is_shadow_only():
    s = _state(cns=80.0)
    result = compute_deload_need(s)
    assert result.shadow_only is True


def test_tier_mapping():
    from app.logic.deload_need import _tier_from_score
    assert _tier_from_score(0.20) == "none"
    assert _tier_from_score(0.40) == "watch"
    assert _tier_from_score(0.60) == "bias"
    assert _tier_from_score(0.80) == "force"
