"""Block → macrocycle auto-link selection (Phase 5 spine, multi-program follow-up).

Non-DB: exercises the pure ``select_block_macrocycle_id`` disambiguator directly,
so it runs locally + in CI regardless of DB availability.
"""
from app.services.planning_service import select_block_macrocycle_id


def test_no_active_macrocycles_returns_none():
    assert select_block_macrocycle_id([], "Strength") is None


def test_single_active_macrocycle_always_attaches():
    # Unambiguous even when the domain doesn't match the block goal.
    assert select_block_macrocycle_id([(7, "running")], "Strength") == 7
    assert select_block_macrocycle_id([(7, None)], "Strength") == 7


def test_many_active_unique_goal_domain_match_attaches():
    candidates = [(1, "running"), (2, "powerlifting")]
    assert select_block_macrocycle_id(candidates, "Powerlifting") == 2
    assert select_block_macrocycle_id(candidates, "Running") == 1


def test_many_active_no_domain_match_stays_null():
    candidates = [(1, "running"), (2, "powerlifting")]
    # A Hypertrophy block matches neither anchor domain → don't guess.
    assert select_block_macrocycle_id(candidates, "Hypertrophy") is None


def test_many_active_ambiguous_multi_match_stays_null():
    # Two programs share the target domain → ambiguous, leave NULL.
    candidates = [(1, "running"), (2, "running")]
    assert select_block_macrocycle_id(candidates, "Running") is None


def test_domain_aliasing_is_applied():
    # BlockGoal "Hyrox"/"CrossFit" canonicalize to "mixed"; an anchor tagged
    # "crossfit" (alias of "mixed") should match a Hyrox block.
    candidates = [(1, "running"), (2, "crossfit")]
    assert select_block_macrocycle_id(candidates, "Hyrox") == 2
