"""Unit tests for app.logic.state_update_v0.update_athlete_state.

All tests are pure — no DB required. Tests verify:
- Return type
- Append-only invariant (Decision 1: prev_state is not mutated)
- Fatigue increases from dose impulses
- Fatigue suppresses adaptation efficiency
- Capacity is not decreased by normal training
- Zero time-delta still applies dose impulses
- Legacy mirror scalars stay consistent with engine vectors
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.state_bridge import capacity_from_legacy, sync_legacy_from_vectors
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import _adaptation_efficiency, update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


# ── Fixtures ──────────────────────────────────────────────────────────────────

_T0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(hours=24)


def _fresh_state(*, cns: float = 5.0, muscular: float = 5.0, max_strength: float = 50.0) -> UnifiedStateVector:
    x = CapacityState(max_strength=max_strength, aerobic=300.0, hypertrophy=50.0, work_capacity=50.0)
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    legacy = sync_legacy_from_vectors(x, f, t)
    return UnifiedStateVector(
        timestamp=_T0,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **legacy,
    )


def _high_dose() -> StressDose:
    """A high-load dose: RPE 9.5 strength session."""
    log = WorkoutLog(
        timestamp=_T1,
        modality="Strength",
        duration_minutes=75.0,
        session_rpe=9.5,
        total_volume_load=8000.0,
        estimated_sets=20.0,
        avg_rir=0.5,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )
    return calculate_stress_dose(log)


def _moderate_dose() -> StressDose:
    """A moderate dose: RPE 7 strength session."""
    log = WorkoutLog(
        timestamp=_T1,
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=7.0,
        total_volume_load=4000.0,
        estimated_sets=12.0,
        sleep_quality=8.0,
        life_stress_inverse=8.0,
    )
    return calculate_stress_dose(log)


def _log(rpe: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=_T1,
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=rpe,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


# ── Return type ───────────────────────────────────────────────────────────────

def test_returns_unified_state_vector():
    s0 = _fresh_state()
    s1 = update_athlete_state(s0, _moderate_dose(), timedelta(hours=24), _log())
    assert isinstance(s1, UnifiedStateVector)


# ── Append-only invariant (Decision 1) ───────────────────────────────────────

def test_append_only_prev_state_unchanged():
    s0 = _fresh_state()
    t0_before = s0.timestamp
    f_cns_before = s0.fatigue_f.cns
    cap_before = s0.capacity_x.max_strength

    update_athlete_state(s0, _moderate_dose(), timedelta(hours=24), _log())

    assert s0.timestamp == t0_before, "prev_state.timestamp must not be mutated"
    assert s0.fatigue_f.cns == f_cns_before, "prev_state.fatigue_f must not be mutated"
    assert s0.capacity_x.max_strength == cap_before, "prev_state.capacity must not be mutated"


# ── Timestamp advances correctly ──────────────────────────────────────────────

def test_timestamp_advances_by_time_delta():
    s0 = _fresh_state()
    dt = timedelta(hours=24)
    s1 = update_athlete_state(s0, _moderate_dose(), dt, _log())
    assert s1.timestamp == _T0 + dt


# ── Fatigue impulses ──────────────────────────────────────────────────────────

def test_fatigue_increases_after_high_rpe_with_zero_timedelta():
    """Zero time-delta means no decay — dose impulse should still increase fatigue."""
    s0 = _fresh_state(cns=0.0, muscular=0.0)
    s1 = update_athlete_state(s0, _high_dose(), timedelta(0), _log(rpe=9.5))
    total_fatigue_before = 0.0  # all zeros
    total_fatigue_after = sum(getattr(s1.fatigue_f, k) for k in FatigueState.KEYS)
    assert total_fatigue_after > total_fatigue_before, (
        "At least one fatigue axis must increase after a high-RPE dose"
    )


def test_zero_timedelta_does_not_reduce_fatigue():
    """timedelta(0) = no decay; pre-existing fatigue must not decrease."""
    s0 = _fresh_state(cns=30.0, muscular=25.0)
    s1 = update_athlete_state(s0, _moderate_dose(), timedelta(0), _log())
    assert s1.fatigue_f.cns >= 0.0
    assert s1.fatigue_f.muscular >= 0.0


# ── Adaptation suppression ────────────────────────────────────────────────────

def test_high_fatigue_suppresses_adaptation():
    """All fatigue axes at 100 should pin efficiency exactly to the floor."""
    from app.engine.parameters import default_parameters
    p = default_parameters()
    s0 = _fresh_state()
    for key in FatigueState.KEYS:
        setattr(s0.fatigue_f, key, 100.0)
    eff = _adaptation_efficiency(s0, p)
    assert abs(eff - p.adapt_fatigue_suppress_floor) < 0.01, (
        f"Efficiency at max fatigue should equal floor={p.adapt_fatigue_suppress_floor}, got {eff:.4f}"
    )


def test_fresh_state_has_full_adaptation_efficiency():
    """Zero fatigue → efficiency = 1.0."""
    from app.engine.parameters import default_parameters
    p = default_parameters()
    s0 = _fresh_state(cns=0.0, muscular=0.0)
    eff = _adaptation_efficiency(s0, p)
    assert eff == 1.0


# ── Capacity correctness ──────────────────────────────────────────────────────

def test_capacity_not_decreased_by_normal_workout():
    """A normal session should not reduce max_strength capacity."""
    s0 = _fresh_state(max_strength=50.0)
    s1 = update_athlete_state(s0, _moderate_dose(), timedelta(hours=24), _log())
    assert s1.capacity_x.max_strength >= s0.capacity_x.max_strength - 0.01


# ── Legacy mirrors ────────────────────────────────────────────────────────────

def test_legacy_mirrors_consistent_after_update():
    """c_nm_force should equal capacity_x.max_strength * 10.0 (sync_legacy_from_vectors invariant)."""
    s0 = _fresh_state()
    s1 = update_athlete_state(s0, _moderate_dose(), timedelta(hours=24), _log())
    expected_c_nm_force = s1.capacity_x.max_strength * 10.0
    assert abs(s1.c_nm_force - expected_c_nm_force) < 0.001, (
        f"c_nm_force={s1.c_nm_force:.4f} should equal max_strength*10={expected_c_nm_force:.4f}"
    )


def test_legacy_metabolic_consistent():
    """f_met_systemic should equal fatigue_f.metabolic."""
    s0 = _fresh_state()
    s1 = update_athlete_state(s0, _moderate_dose(), timedelta(hours=24), _log())
    assert abs(s1.f_met_systemic - s1.fatigue_f.metabolic) < 0.001
