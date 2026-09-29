"""Macrocycle "week X of Y" schedule math (Phase 5 — goal-anchored program).

Non-DB: exercises the pure ``compute_week_progress`` helper with an injected
``today`` so it is deterministic and runs locally + in CI regardless of DB
availability (see tests/test_macrocycles_routes.py for the DB-gated round trip).
"""
from datetime import date, timedelta

from app.logic.planning import block_taper_week
from app.services.macrocycle_service import (
    block_end_date,
    block_phase,
    compute_unplanned_weeks,
    compute_week_progress,
)

TODAY = date(2026, 7, 4)


def test_mid_program():
    wp = compute_week_progress(TODAY - timedelta(days=14), TODAY + timedelta(days=14), today=TODAY)
    assert wp.current_week == 3          # 14 days in → week 3
    assert wp.total_weeks == 4           # 28-day span
    assert wp.pct == 75.0
    assert wp.weeks_to_go == 2


def test_first_day_is_week_one():
    wp = compute_week_progress(TODAY, TODAY + timedelta(days=56), today=TODAY)
    assert wp.current_week == 1
    assert wp.total_weeks == 8
    assert wp.pct == 12.5
    assert wp.weeks_to_go == 8


def test_not_yet_started_clamps_to_week_one():
    wp = compute_week_progress(TODAY + timedelta(days=7), TODAY + timedelta(days=35), today=TODAY)
    assert wp.current_week == 1          # start is in the future → still week 1
    assert wp.total_weeks == 4


def test_overrun_caps_current_and_weeks_to_go_nonnegative():
    wp = compute_week_progress(TODAY - timedelta(days=70), TODAY - timedelta(days=7), today=TODAY)
    assert wp.total_weeks == 9           # 63-day span
    assert wp.current_week == 9          # capped — never "week 11 of 9"
    assert wp.pct == 100.0
    assert wp.weeks_to_go == 0           # target already passed, never negative


def test_at_target_date():
    wp = compute_week_progress(TODAY - timedelta(days=28), TODAY, today=TODAY)
    assert wp.total_weeks == 4
    assert wp.current_week == 4
    assert wp.pct == 100.0
    assert wp.weeks_to_go == 0


def test_open_horizon_when_no_target():
    wp = compute_week_progress(TODAY - timedelta(days=14), None, today=TODAY)
    assert wp.current_week == 3          # still counts up
    assert wp.total_weeks is None
    assert wp.pct is None
    assert wp.weeks_to_go is None


def test_target_on_or_before_start_is_open_horizon():
    wp = compute_week_progress(TODAY, TODAY - timedelta(days=1), today=TODAY)
    assert wp.current_week == 1
    assert wp.total_weeks is None
    assert wp.pct is None


# ---------------------------------------------------------------------------
# Program timeline helpers (B2)
# ---------------------------------------------------------------------------

def test_block_end_date_prefers_stored_else_weeks_times_seven_minus_one():
    assert block_end_date(TODAY, 4, None) == TODAY + timedelta(days=27)
    assert block_end_date(TODAY, 4, TODAY + timedelta(days=3)) == TODAY + timedelta(days=3)


def test_block_phase_boundaries():
    end = TODAY + timedelta(days=6)
    assert block_phase(TODAY, end, today=TODAY) == "current"         # first day
    assert block_phase(TODAY, end, today=end) == "current"           # last day (inclusive)
    assert block_phase(TODAY, end, today=end + timedelta(days=1)) == "completed"
    assert block_phase(TODAY, end, today=TODAY - timedelta(days=1)) == "upcoming"


def test_block_taper_week_is_final_week_of_three_plus_week_blocks():
    assert block_taper_week(1) is None
    assert block_taper_week(2) is None
    assert block_taper_week(3) == 3
    assert block_taper_week(8) == 8


def test_unplanned_weeks():
    # No target → null, blocks or not.
    assert compute_unplanned_weeks(TODAY, None, None) is None
    assert compute_unplanned_weeks(TODAY, TODAY + timedelta(days=13), None) is None
    # No blocks → the whole horizon from the macrocycle start (matches total_weeks).
    assert compute_unplanned_weeks(TODAY, None, TODAY + timedelta(days=28)) == 4
    # Blocks cover through the day before the target → nothing unplanned.
    assert compute_unplanned_weeks(TODAY, TODAY + timedelta(days=27), TODAY + timedelta(days=28)) == 0
    # A partial uncovered week rounds up; blocks past the target never go negative.
    assert compute_unplanned_weeks(TODAY, TODAY + timedelta(days=13), TODAY + timedelta(days=24)) == 2
    assert compute_unplanned_weeks(TODAY, TODAY + timedelta(days=40), TODAY + timedelta(days=28)) == 0
