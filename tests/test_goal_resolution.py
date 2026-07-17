"""Unit tests for the goal-resolution ordering (no DB required).

This unit covers the precedence logic --
active block goal > explicit query goal > profile.primary_goal > default --
independent of any database, complementing the DB-backed persisted-goal tests
(tests/test_persisted_goal.py), which run against the session-scoped test schema
whenever a database is available.
"""
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.prescription_service import resolve_effective_goal


def test_block_goal_wins_over_everything():
    resolved = resolve_effective_goal(
        block_goal="Hypertrophy",
        query_goal="Power",
        profile_goal="Powerlifting",
    )
    assert resolved == "Hypertrophy"


def test_query_goal_wins_when_no_active_block():
    resolved = resolve_effective_goal(
        block_goal=None,
        query_goal="Power",
        profile_goal="Powerlifting",
    )
    assert resolved == "Power"


def test_profile_goal_wins_when_no_block_and_no_query():
    resolved = resolve_effective_goal(
        block_goal=None,
        query_goal=None,
        profile_goal="Powerlifting",
    )
    assert resolved == "Powerlifting"


def test_default_used_when_nothing_else_set():
    resolved = resolve_effective_goal(
        block_goal=None,
        query_goal=None,
        profile_goal=None,
    )
    assert resolved == TRAINING_GOAL_DEFAULT
