"""A goal's default week yields exactly the sessions the athlete asked for (planner cap fix).

``BlockCreateRequest.sessions_per_week`` allows 1-7 (app/schemas/planning.py), but every
goal default authors 3 days and ``_default_template_for_goal`` returned ``slots[:n]``: a
5-day Strength block silently got 3 days.

The fill rule is the smallest one that invents nothing: the authored days stay exactly as
they are, and each extra session repeats the goal's authored categories in order, placed on
the first free day of the planner's existing spacing order (``_WEEK_DAY_ORDER``, the rule the
modality-mix path already uses). No new categories, no new spacing rule.
"""

from __future__ import annotations

import pytest

from app.models.mesocycle import BlockGoal
from app.services.planning_service import (
    _DEFAULT_TEMPLATES,
    _WEEK_DAY_ORDER,
    _default_template_for_goal,
)


@pytest.mark.parametrize("goal", list(BlockGoal))
@pytest.mark.parametrize("n", [4, 5, 6, 7])
def test_a_default_week_has_exactly_the_requested_sessions_on_distinct_valid_days(
    goal: BlockGoal, n: int
) -> None:
    week = _default_template_for_goal(goal, n)
    days = [s.day_of_week for s in week]
    assert len(week) == n
    assert len(set(days)) == n, days
    assert all(1 <= d <= 7 for d in days)
    # Sorted by day: create_block_with_sessions takes the LAST slot as the benchmark day.
    assert days == sorted(days)


@pytest.mark.parametrize("goal", list(BlockGoal))
@pytest.mark.parametrize("n", [1, 2, 3])
def test_one_to_three_sessions_are_unchanged(goal: BlockGoal, n: int) -> None:
    assert _default_template_for_goal(goal, n) == _DEFAULT_TEMPLATES[goal][:n]


@pytest.mark.parametrize("goal", list(BlockGoal))
@pytest.mark.parametrize("n", [4, 5, 7])
def test_extra_sessions_only_repeat_authored_categories(goal: BlockGoal, n: int) -> None:
    authored = _DEFAULT_TEMPLATES[goal]
    week = _default_template_for_goal(goal, n)
    pairs = {(s.category, s.modality, s.domain) for s in authored}
    assert {(s.category, s.modality, s.domain) for s in week} <= pairs
    # Every authored day is still there, untouched.
    for slot in authored:
        assert slot in week


def test_strength_five_days_cycles_the_authored_categories_in_order() -> None:
    week = _default_template_for_goal(BlockGoal.STRENGTH, 5)
    by_day = {s.day_of_week: s.category for s in week}
    # Authored: 1 Max, 3 Volume, 5 Accessory. Extras go to the first free days of
    # _WEEK_DAY_ORDER (2, then 4) and repeat the authored order: Max, then Volume.
    assert by_day == {
        1: "Max Strength",
        2: "Max Strength",
        3: "Strength — Volume",
        4: "Strength — Volume",
        5: "Accessory Focus",
    }


def test_hyrox_four_days_uses_the_first_free_day_of_the_spacing_order() -> None:
    week = _default_template_for_goal(BlockGoal.HYROX, 4)
    by_day = {s.day_of_week: s.category for s in week}
    # Authored days 1, 3, 6; the first free day in (1, 3, 5, 2, 4, 6, 7) is 5.
    assert by_day == {
        1: "Strength Endurance",
        3: "Running + Functional",
        5: "Strength Endurance",
        6: "Hyrox Simulation",
    }


def test_the_spacing_order_covers_every_weekday() -> None:
    assert sorted(_WEEK_DAY_ORDER) == [1, 2, 3, 4, 5, 6, 7]
