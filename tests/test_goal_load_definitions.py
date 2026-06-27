"""Tests for goal load definitions — completeness and data integrity."""

import pytest
from typing import get_args

from app.schemas.training_goals import TrainingGoal
from app.logic.goal_load_definitions import (
    GoalLoadDefinition,
    GOAL_LOAD_DEFINITIONS,
    get_goal_load_definition,
)

ALL_GOALS: tuple[str, ...] = get_args(TrainingGoal)

ANCHOR_FIELDS = (
    "goal_specific_training_load",
    "primary_capacity_anchor",
    "load_tolerance_anchor",
    "risk_or_tissue_anchor",
    "best_retest_metric",
)


def test_every_training_goal_has_exactly_one_definition() -> None:
    missing = [g for g in ALL_GOALS if g not in GOAL_LOAD_DEFINITIONS]
    assert not missing, f"Missing GoalLoadDefinition for: {missing}"
    assert len(GOAL_LOAD_DEFINITIONS) == len(ALL_GOALS), (
        f"Definition count {len(GOAL_LOAD_DEFINITIONS)} != "
        f"goal count {len(ALL_GOALS)}"
    )


@pytest.mark.parametrize("goal", ALL_GOALS)
def test_no_empty_anchor_fields(goal: str) -> None:
    defn = GOAL_LOAD_DEFINITIONS[goal]
    for field in ANCHOR_FIELDS:
        value = defn[field]  # type: ignore[literal-required]
        assert value and value.strip(), (
            f"Goal '{goal}' has empty field '{field}'"
        )


@pytest.mark.parametrize("goal", ALL_GOALS)
def test_goal_field_matches_key(goal: str) -> None:
    defn = GOAL_LOAD_DEFINITIONS[goal]
    assert defn["goal"] == goal


def test_get_goal_load_definition_returns_correct_goal() -> None:
    for goal in ALL_GOALS:
        defn = get_goal_load_definition(goal)  # type: ignore[arg-type]
        assert defn["goal"] == goal


def test_definition_has_all_required_keys() -> None:
    required = {"goal"} | set(ANCHOR_FIELDS)
    for goal, defn in GOAL_LOAD_DEFINITIONS.items():
        missing_keys = required - set(defn.keys())
        assert not missing_keys, f"Goal '{goal}' missing keys: {missing_keys}"
