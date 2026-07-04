"""Dashboard Overview tile math (Phase 6 — real training-load/ACWR + adherence).

Non-DB: exercises the pure helpers in app.services.dashboard_service directly
(daily_load, compute_training_load, compute_adherence_pct, compute_streak), so
this module runs locally and in CI regardless of DB availability. See
tests/test_dashboard_overview_routes.py for the DB-gated round trip.
"""
from datetime import date, timedelta

from app.services.dashboard_service import (
    ACUTE_DAYS,
    compute_adherence_pct,
    compute_streak,
    compute_training_load,
    daily_load,
)

TODAY = date(2026, 7, 4)


# --- daily_load ------------------------------------------------------------

def test_daily_load_is_rpe_times_duration():
    assert daily_load(8.0, 60.0) == 480.0


def test_daily_load_falls_back_to_duration_when_rpe_missing():
    assert daily_load(None, 45.0) == 45.0
    assert daily_load(0.0, 45.0) == 45.0


def test_daily_load_zero_when_no_duration():
    assert daily_load(8.0, 0.0) == 0.0
    assert daily_load(8.0, None) == 0.0


# --- compute_training_load -------------------------------------------------

def _loads_flat(per_day: float, days: int) -> dict[date, float]:
    """A constant daily load stretching `days` back from TODAY (inclusive)."""
    return {TODAY - timedelta(days=i): per_day for i in range(days)}


def test_training_load_insufficient_when_empty():
    tl = compute_training_load({}, TODAY)
    assert tl.status == "insufficient"
    assert tl.acwr is None and tl.acute is None and tl.chronic is None


def test_training_load_insufficient_when_history_span_too_short():
    # Only the last 5 days have data — no chronic baseline predating the acute window.
    tl = compute_training_load(_loads_flat(100.0, 5), TODAY)
    assert tl.status == "insufficient"
    assert tl.acwr is None


def test_training_load_optimal_on_steady_state():
    # Constant load for 28 days → acute == chronic-weekly → acwr == 1.0.
    tl = compute_training_load(_loads_flat(100.0, 28), TODAY)
    assert tl.acwr == 1.0
    assert tl.status == "optimal"
    # acute = 7 * 100 = 700; chronic weekly = (28*100)/4 = 700
    assert tl.acute == 700.0
    assert tl.chronic == 700.0


def test_training_load_high_on_acute_spike():
    loads = _loads_flat(50.0, 28)
    # Pile extra load into the last 3 days → acute spikes above chronic.
    for i in range(3):
        loads[TODAY - timedelta(days=i)] += 400.0
    tl = compute_training_load(loads, TODAY)
    assert tl.acwr is not None and tl.acwr > 1.3
    assert tl.status == "high"


def test_training_load_low_when_acute_below_baseline():
    # Full load 8-28 days ago, but nothing in the last 7 days → acwr < 0.8.
    loads = {TODAY - timedelta(days=i): 100.0 for i in range(ACUTE_DAYS, 28)}
    tl = compute_training_load(loads, TODAY)
    assert tl.acute == 0.0
    assert tl.acwr == 0.0
    assert tl.status == "low"


def test_training_load_sweet_spot_bounds_exposed():
    tl = compute_training_load(_loads_flat(100.0, 28), TODAY)
    assert tl.sweet_spot_low == 0.8
    assert tl.sweet_spot_high == 1.3


# --- compute_adherence_pct -------------------------------------------------

def test_adherence_pct_basic():
    assert compute_adherence_pct(3, 4) == 75.0


def test_adherence_pct_full():
    assert compute_adherence_pct(5, 5) == 100.0


def test_adherence_pct_null_when_nothing_scheduled():
    assert compute_adherence_pct(0, 0) is None


# --- compute_streak --------------------------------------------------------

def test_streak_counts_back_from_today():
    active = {TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert compute_streak(active, TODAY) == 3


def test_streak_survives_inactive_today_resuming_from_yesterday():
    active = {TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert compute_streak(active, TODAY) == 2


def test_streak_breaks_on_gap_day():
    active = {TODAY, TODAY - timedelta(days=2)}  # yesterday missing
    assert compute_streak(active, TODAY) == 1


def test_streak_zero_when_no_recent_activity():
    active = {TODAY - timedelta(days=5)}
    assert compute_streak(active, TODAY) == 0


def test_streak_zero_when_empty():
    assert compute_streak(set(), TODAY) == 0
