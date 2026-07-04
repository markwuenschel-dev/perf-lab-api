"""Objective progress math (Phase 4a — goal-anchored program plan).

Non-DB: exercises the pure ``compute_progress_pct`` / ``days_to_go`` helpers
directly (app.services.objective_service), so this test module runs locally
and in CI regardless of DB availability (see tests/test_objectives_routes.py
for the DB-gated round trip).
"""
from datetime import date, timedelta

from app.services.objective_service import compute_progress_pct, days_to_go


def test_lower_is_better_current_below_target_is_high_pct():
    """A run time (better_direction="lower") already faster than target."""
    pct = compute_progress_pct(current=170.0, target=180.0, better_direction="lower")
    assert pct is not None
    assert pct == 100.0  # clamped — already beat the goal


def test_lower_is_better_current_above_target_is_partial_pct():
    pct = compute_progress_pct(current=200.0, target=180.0, better_direction="lower")
    assert pct is not None
    assert 80.0 < pct < 100.0
    assert pct == 90.0  # 180 / 200 * 100


def test_higher_is_better_current_below_target_is_low_pct():
    """A lift (better_direction="higher") not yet at the target load."""
    pct = compute_progress_pct(current=180.0, target=220.0, better_direction="higher")
    assert pct is not None
    assert pct < 100.0
    assert round(pct, 1) == round(180.0 / 220.0 * 100.0, 1)


def test_higher_is_better_current_at_or_above_target_is_full_pct():
    pct = compute_progress_pct(current=225.0, target=220.0, better_direction="higher")
    assert pct == 100.0  # clamped


def test_free_text_objective_has_null_progress():
    """No benchmark link (None inputs) → null progress, not an exception."""
    assert compute_progress_pct(current=None, target=None, better_direction=None) is None
    assert compute_progress_pct(current=None, target=180.0, better_direction="lower") is None
    assert compute_progress_pct(current=170.0, target=None, better_direction="lower") is None


def test_unrecognized_direction_is_null():
    assert compute_progress_pct(current=170.0, target=180.0, better_direction="sideways") is None


def test_nonpositive_denominator_is_null_not_a_crash():
    assert compute_progress_pct(current=0.0, target=180.0, better_direction="lower") is None
    assert compute_progress_pct(current=180.0, target=0.0, better_direction="higher") is None


def test_days_to_go_counts_from_today():
    target = date.today() + timedelta(days=10)
    assert days_to_go(target) == 10


def test_days_to_go_null_when_no_target_date():
    assert days_to_go(None) is None


def test_days_to_go_can_be_negative_for_past_dates():
    target = date.today() - timedelta(days=3)
    assert days_to_go(target) == -3
