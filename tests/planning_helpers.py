"""Helpers for tests that build planned sessions by hand instead of through block creation."""
from datetime import date


def block_day_of(block_start: date, plan_date: date) -> int:
    """The block-relative slot (1-7) block creation gives a session planned for ``plan_date`` in a
    block that starts on ``block_start``: day N of its block week (ADR-0074)."""
    return (plan_date - block_start).days % 7 + 1
