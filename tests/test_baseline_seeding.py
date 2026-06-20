"""Baseline state seeding from onboarding inputs (§2.3)."""

from app.services.state_service import (
    _aerobic_from_run_5k,
    _baseline_skill_state,
    _build_baseline_vector,
    _habit_strength_from_experience,
)


def test_habit_strength_from_experience():
    assert _habit_strength_from_experience(0.0) == 0.3
    assert abs(_habit_strength_from_experience(3.0) - 0.6) < 1e-9
    assert _habit_strength_from_experience(20.0) == 0.85  # clamped


def test_skill_state_seed_from_level_and_one_rm():
    s = _baseline_skill_state("beginner", None, None, None)
    assert s == {"squat": 0.35, "deadlift": 0.35, "bench": 0.35}

    bumped = _baseline_skill_state("intermediate", 140.0, None, None)
    assert bumped["squat"] == 0.65  # supplied 1RM bumps the pattern
    assert bumped["deadlift"] == 0.55
    assert "bench" in bumped  # bench seeded so the bench path isn't blind


def test_aerobic_from_run_5k_monotonic_and_clamped():
    assert _aerobic_from_run_5k(800.0) == 650.0   # faster than anchor → ceiling
    assert _aerobic_from_run_5k(2200.0) == 180.0  # slower than anchor → floor
    assert 180.0 < _aerobic_from_run_5k(1500.0) < 650.0


def test_build_baseline_threads_run_time_and_experience():
    u_no_run, _ = _build_baseline_vector(1, experience_level="intermediate")
    u_fast, _ = _build_baseline_vector(1, experience_level="intermediate", run_5k_seconds=900.0)
    assert u_fast.capacity_x.aerobic > u_no_run.capacity_x.aerobic

    u_exp, _ = _build_baseline_vector(1, experience_years=4.0)
    assert u_exp.habit_strength == _habit_strength_from_experience(4.0)
