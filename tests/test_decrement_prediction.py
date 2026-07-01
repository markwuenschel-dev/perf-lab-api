from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.decrement_prediction import compute_decrement_prediction
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose


def _state(cns: float = 0.0, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _dose(volume: float = 0.5, intensity: float = 0.5) -> StressDose:
    return StressDose(
        dose_six=StressDoseSix(volume=volume, intensity=intensity, density=0.5, impact=0.3, skill=0.2, metabolic=0.4),
        adaptation_contribution=AdaptationContribution(),
        d_nm_central=2.0, d_nm_peripheral=1.5, d_met_systemic=1.0, d_struct_damage=0.5,
    )


def test_fresh_state_low_decrement_score():
    result = compute_decrement_prediction(_dose(), _state(cns=5.0, muscular=5.0), time_gap_hours=48.0)
    assert result.score < 0.40, f"Fresh state should have low decrement score, got {result.score:.3f}"
    assert result.shadow_only is True


def test_high_cns_fatigue_raises_score():
    fresh = compute_decrement_prediction(_dose(), _state(cns=10.0), time_gap_hours=48.0)
    tired = compute_decrement_prediction(_dose(), _state(cns=80.0), time_gap_hours=48.0)
    assert tired.score > fresh.score


def test_short_gap_raises_score():
    long_gap = compute_decrement_prediction(_dose(), _state(), time_gap_hours=72.0)
    short_gap = compute_decrement_prediction(_dose(), _state(), time_gap_hours=6.0)
    assert short_gap.score > long_gap.score


def test_high_previous_dose_raises_score():
    low_dose = compute_decrement_prediction(_dose(volume=0.2, intensity=0.3), _state(), time_gap_hours=48.0)
    high_dose = compute_decrement_prediction(_dose(volume=2.0, intensity=1.5), _state(), time_gap_hours=48.0)
    assert high_dose.score > low_dose.score


def test_drivers_populated_under_high_load():
    result = compute_decrement_prediction(_dose(), _state(cns=70.0), time_gap_hours=48.0)
    assert len(result.drivers) > 0


def test_score_bounded():
    result = compute_decrement_prediction(
        _dose(volume=5.0, intensity=5.0), _state(cns=100.0, muscular=100.0), time_gap_hours=1.0
    )
    assert 0.0 <= result.score <= 1.0


def test_higher_planned_difficulty_raises_score():
    """planned_next_difficulty must be wired into the score, not just drivers."""
    dose = _dose()
    state = _state(cns=20.0, muscular=10.0)
    low = compute_decrement_prediction(dose, state, planned_next_difficulty=0.2, time_gap_hours=48.0)
    high = compute_decrement_prediction(dose, state, planned_next_difficulty=0.9, time_gap_hours=48.0)
    assert high.score > low.score, (
        f"Higher planned difficulty should raise decrement score: {high.score:.3f} vs {low.score:.3f}"
    )


def test_high_cns_fatigue_in_affected_axes():
    """CNS fatigue above threshold must appear in affected_axes as 'cns'."""
    result = compute_decrement_prediction(_dose(), _state(cns=70.0), time_gap_hours=48.0)
    assert "cns" in result.affected_axes, (
        f"Expected 'cns' in affected_axes, got {result.affected_axes}"
    )
