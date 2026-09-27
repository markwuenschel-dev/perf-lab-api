"""A goal's default week yields exactly the sessions the athlete asked for (planner cap fix).

``BlockCreateRequest.sessions_per_week`` allows 1-7 (app/schemas/planning.py), but every
goal default authors 3 days and ``_default_template_for_goal`` returned ``slots[:n]``: a
5-day Strength block silently got 3 days.

The fill rule (user decision, 2026-09-27): preserve the relative order of the authored
category sequence, but distribute repeated instances as evenly as practical across the week.
No new categories and no sport-specific periodization, only a deterministic spacing rule.
Moving the authored days is accepted for 4+ sessions. Nothing here claims the arrangement is
physiologically optimal; it only avoids clustering identical sessions when that is avoidable.

"Adjacent" means consecutive sessions in day order, INCLUDING the week wrap: a block repeats
its week, so the last session and the next week's first are consecutive exposures too.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.models.mesocycle import BlockGoal
from app.services.planning_service import _DEFAULT_TEMPLATES, _default_template_for_goal

GOALS = list(BlockGoal)
EXTRA = [4, 5, 6, 7]


def _categories(goal: BlockGoal, n: int) -> list[str]:
    return [s.category for s in _default_template_for_goal(goal, n)]


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
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


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", [1, 2, 3])
def test_one_to_three_sessions_are_unchanged(goal: BlockGoal, n: int) -> None:
    assert _default_template_for_goal(goal, n) == _DEFAULT_TEMPLATES[goal][:n]


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
def test_extra_sessions_only_repeat_authored_categories(goal: BlockGoal, n: int) -> None:
    pairs = {(s.category, s.modality, s.domain) for s in _DEFAULT_TEMPLATES[goal]}
    week = _default_template_for_goal(goal, n)
    assert {(s.category, s.modality, s.domain) for s in week} <= pairs


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
def test_no_category_repeats_on_consecutive_sessions_whenever_avoidable(
    goal: BlockGoal, n: int
) -> None:
    """A cyclic arrangement of n sessions with no two neighbours equal exists iff no
    category needs more than floor(n/2) copies. With 3 authored categories and balanced
    counts the most any category needs is ceil(n/3), and ceil(n/3) <= floor(n/2) for every
    n from 4 to 7. So it is always avoidable here, and asserted rather than assumed."""
    cats = _categories(goal, n)
    worst = max(Counter(cats).values())
    assert worst <= n // 2, "no non-adjacent arrangement exists; the property would not apply"
    neighbours = list(zip(cats, cats[1:] + cats[:1], strict=True))
    assert all(a != b for a, b in neighbours), cats


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
def test_the_authored_order_of_first_occurrences_is_kept(goal: BlockGoal, n: int) -> None:
    cats = _categories(goal, n)
    first_seen = list(dict.fromkeys(cats))
    assert first_seen == [s.category for s in _DEFAULT_TEMPLATES[goal]]


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
def test_repeats_are_balanced(goal: BlockGoal, n: int) -> None:
    counts = Counter(_categories(goal, n))
    assert set(counts) == {s.category for s in _DEFAULT_TEMPLATES[goal]}
    assert max(counts.values()) - min(counts.values()) <= 1


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("n", EXTRA)
def test_the_week_is_deterministic(goal: BlockGoal, n: int) -> None:
    assert _default_template_for_goal(goal, n) == _default_template_for_goal(goal, n)


def test_strength_five_days_spreads_its_repeats() -> None:
    week = _default_template_for_goal(BlockGoal.STRENGTH, 5)
    by_day = {s.day_of_week: s.category for s in week}
    # Not Max, Max, Volume, Volume, Accessory: the repeats are spaced out.
    assert by_day == {
        1: "Max Strength",
        2: "Strength — Volume",
        4: "Max Strength",
        5: "Accessory Focus",
        7: "Strength — Volume",
    }
