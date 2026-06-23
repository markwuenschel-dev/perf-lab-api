"""Unit tests for the readiness combine rule (ADR-0026) — no DB required.

These exercise the pure functions that hold the combine rule, so the math is
verified locally even without a live Postgres.
"""

import pytest

from app.services.readiness_service import (
    WELLNESS_WEIGHT,
    combine_readiness,
    wellness_modifier,
)


def test_no_signals_is_neutral():
    modifier, components = wellness_modifier({}, {})
    assert modifier == 0.0
    assert components == []


def test_all_at_baseline_is_neutral():
    # value == default baseline for every signal -> zero contribution each.
    values = {"hrv_ms": 60.0, "sleep_hours": 8.0, "sleep_quality": 85.0,
              "resting_hr": 55.0, "soreness": 3.0, "mood": 6.0}
    modifier, components = wellness_modifier(values, {})
    assert abs(modifier) < 1e-9
    assert len(components) == len(values)
    assert all(abs(c.contribution) < 1e-9 for c in components)


def test_good_day_is_positive_bounded():
    # Better-than-baseline across the board: higher HRV/sleep/mood, lower RHR/soreness.
    values = {"hrv_ms": 120.0, "sleep_hours": 12.0, "sleep_quality": 100.0,
              "resting_hr": 35.0, "soreness": 0.0, "mood": 10.0}
    modifier, _ = wellness_modifier(values, {})
    assert modifier > 0.0
    assert modifier <= WELLNESS_WEIGHT + 1e-9  # bounded


def test_bad_day_is_negative_bounded():
    values = {"hrv_ms": 20.0, "sleep_hours": 3.0, "sleep_quality": 40.0,
              "resting_hr": 80.0, "soreness": 9.0, "mood": 2.0}
    modifier, _ = wellness_modifier(values, {})
    assert modifier < 0.0
    assert modifier >= -WELLNESS_WEIGHT - 1e-9


def test_direction_signs():
    # Lower-is-better signals must contribute positively when below baseline.
    mod_rhr, comps_rhr = wellness_modifier({"resting_hr": 45.0}, {})
    assert mod_rhr > 0.0 and comps_rhr[0].contribution > 0.0
    mod_sore, _ = wellness_modifier({"soreness": 9.0}, {})
    assert mod_sore < 0.0


def test_personal_baseline_overrides_default():
    # A high absolute HRV that is *below* this athlete's personal baseline reads
    # as a negative signal, even though it beats the population default.
    modifier, _ = wellness_modifier({"hrv_ms": 80.0}, {"hrv_ms": 100.0})
    assert modifier < 0.0


def test_contribution_is_clamped():
    # An extreme value cannot dominate beyond a single full unit.
    _, components = wellness_modifier({"hrv_ms": 10_000.0}, {})
    assert components[0].contribution == 1.0


def test_combine_anchors_on_model_and_clamps():
    assert combine_readiness(0.7, 0.1) == pytest.approx(0.8)
    assert combine_readiness(0.95, 0.15) == 1.0   # clamped high
    assert combine_readiness(0.05, -0.15) == 0.0  # clamped low
