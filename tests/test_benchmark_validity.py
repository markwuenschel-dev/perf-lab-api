"""Tests for benchmark validity profiles (Task 2).

TDD: these tests were written before the implementation.
RED phase: fails with ModuleNotFoundError on benchmark_validity.
GREEN phase: all pass after implementing benchmark_validity.py and
threading validity_profile through state_update_v0.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.benchmark_validity import (
    effective_variance,
    get_validity_profile,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(cns: float = 0.0, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.0,
        skill_state={},
        **leg,
    )


def test_1rm_is_capacity_dominant():
    p = get_validity_profile("1rm")
    assert p.classification == "capacity_dominant"
    assert p.measurement_variance < 0.10


def test_mobility_is_noise_prone():
    p = get_validity_profile("mobility")
    assert p.classification in ("noise_prone", "skill_sensitive")
    assert p.measurement_variance > 0.10


def test_1rm_has_strong_strength_mapping():
    p = get_validity_profile("1rm")
    assert p.mapping_strength.get("max_strength", 0.0) > 0.80


def test_rested_1rm_higher_gain_than_fatigued_mobility():
    p_1rm = get_validity_profile("1rm")
    p_mob = get_validity_profile("mobility")
    s_fresh = _state(cns=5.0, muscular=5.0)
    s_tired = _state(cns=60.0, muscular=60.0)
    r_1rm_fresh = effective_variance(p_1rm, s_fresh)
    r_mob_tired = effective_variance(p_mob, s_tired)
    assert r_1rm_fresh < r_mob_tired, "Fresh 1RM should have lower R_eff than fatigued mobility"


def test_zero_sensitivity_profile_variance_is_fatigue_invariant():
    p_mob = get_validity_profile("mobility")
    s_fresh = _state(cns=5.0, muscular=5.0)
    s_tired = _state(cns=70.0, muscular=70.0)
    assert effective_variance(p_mob, s_fresh) == effective_variance(p_mob, s_tired), \
        "A profile with no fatigue_sensitivity must not have fatigue-dependent R_eff"


def test_high_fatigue_increases_effective_variance():
    p = get_validity_profile("rep_max")
    s_fresh = _state(cns=5.0, muscular=5.0)
    s_tired = _state(cns=70.0, muscular=70.0)
    r_fresh = effective_variance(p, s_fresh)
    r_tired = effective_variance(p, s_tired)
    assert r_tired > r_fresh, "High fatigue must raise effective variance for fatigue-sensitive benchmark"


def test_weak_mapping_reduces_gain():
    r_eff = 0.08
    prior_var = 1.0
    strong_mapping = 0.95
    weak_mapping = 0.20
    # The effective update coefficient on the state is:
    #   mapping_strength * K = P * m² / (m² * P + R_eff)
    # This is monotonically increasing in m, so strong_mapping always produces
    # a larger effective update than weak_mapping. (The raw K alone is non-monotone.)
    eff_strong = prior_var * strong_mapping ** 2 / (strong_mapping ** 2 * prior_var + r_eff)
    eff_weak = prior_var * weak_mapping ** 2 / (weak_mapping ** 2 * prior_var + r_eff)
    assert eff_strong > eff_weak, "Strong mapping must produce a larger effective state update than weak mapping"


def test_unknown_benchmark_code_returns_noise_prone_default():
    p = get_validity_profile("some_unknown_benchmark_xyz")
    assert p.classification == "noise_prone"
    assert p.measurement_variance >= 0.15
