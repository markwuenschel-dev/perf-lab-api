"""Volume changes real work, not the clock (phase 2.3).

Volume and intensity are separate prescription variables. Scaling volume moves SETS (and, for
blocks nothing emits yet, repetitions or accumulated work time) and leaves reps, load and
effort targets exactly as authored — 5×3 to 5×8 is not "more volume", it is a different
session.

The deliberately-deferred half is at the bottom: moving the DELOAD from scaling duration to
removing sets is correct prescription semantics and would make production's dose accounting
materially worse, because v0's density is minutes-per-set. That wait is pinned by a test so it
cannot be forgotten.
"""
import pytest

from app.logic.prescriber import INTENSITY_SET_DOMAINS, recommend_next_session
from app.schemas.prescription import project_exercises
from app.schemas.workout_structure import (
    ContinuousBlock,
    CooldownBlock,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    adjust_strength_sets,
    apply_volume_modifier,
)


def _squat(sets: int = 5) -> StrengthBlock:
    return StrengthBlock(
        exercise="Back Squat", sets=sets, reps="5", percent_e1rm=0.8, rpe_target=8.0,
        rest_sec=180,
    )


# ── the transformation ───────────────────────────────────────────────────────

def test_a_modifier_of_one_is_exactly_the_identity() -> None:
    """Exactly, not approximately: no rounding, no copies that differ by a field."""
    structure = [_squat(5), WarmupBlock(duration_sec=600), IntervalBlock(repetitions=4)]

    assert apply_volume_modifier(structure, 1.0) == structure


def test_volume_moves_sets_and_leaves_the_prescription_alone() -> None:
    halved = apply_volume_modifier([_squat(6)], 0.5)[0]

    assert halved.sets == 3
    assert halved.reps == "5", "reps are not a volume control"
    assert halved.percent_e1rm == 0.8 and halved.rpe_target == 8.0, "intensity is untouched"
    assert halved.rest_sec == 180


@pytest.mark.parametrize(
    ("sets", "modifier", "expected"),
    [(5, 0.5, 3), (4, 0.5, 2), (5, 0.6, 3), (12, 0.6, 7), (3, 2.0, 6), (5, 0.01, 1)],
)
def test_set_scaling_rounds_half_up_and_never_below_one(sets, modifier, expected) -> None:
    """Half away from zero, so the athlete keeps the work on a tie; floor of one set."""
    assert apply_volume_modifier([_squat(sets)], modifier)[0].sets == expected


def test_warmups_and_cooldowns_are_not_training_volume() -> None:
    structure = [WarmupBlock(duration_sec=600), CooldownBlock(duration_sec=300)]

    assert apply_volume_modifier(structure, 0.5) == structure


def test_the_dormant_block_kinds_scale_the_right_quantity() -> None:
    """Nothing emits these yet; the interface is fixed now so phase 5 adds behaviour only."""
    intervals = apply_volume_modifier(
        [IntervalBlock(repetitions=6, work_duration_sec=180, intensity_target=4.0)], 0.5
    )[0]
    steady = apply_volume_modifier([ContinuousBlock(duration_sec=1800)], 0.5)[0]

    assert intervals.repetitions == 3
    assert intervals.work_duration_sec == 180, "interval length is not a volume control"
    assert intervals.intensity_target == 4.0, "and neither is intensity"
    assert steady.duration_sec == 900


def test_modifiers_are_not_algebraically_reversible() -> None:
    """Documented, so nobody later treats them as a group operation. 5 -> 3 -> 6."""
    once = apply_volume_modifier([_squat(5)], 0.5)
    back = apply_volume_modifier(once, 2.0)

    assert once[0].sets == 3
    assert back[0].sets == 6, "integer sets do not round-trip, and must not be relied on to"


def test_a_negative_modifier_is_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        apply_volume_modifier([_squat()], -1.0)


def test_step_adjustment_has_a_floor_and_leaves_other_blocks_alone() -> None:
    structure = [_squat(1), IntervalBlock(repetitions=4), WarmupBlock(duration_sec=300)]
    lowered = adjust_strength_sets(structure, -1)

    assert lowered[0].sets == 1, "never below one working set"
    assert lowered[1] == structure[1] and lowered[2] == structure[2]
    assert adjust_strength_sets(structure, 0) == structure


# ── monotonicity through the real prescriber ─────────────────────────────────

def _sets_for(workload: str) -> int:
    rx = recommend_next_session(
        _state(), goal="Strength",
        block_context={
            "block_goal": "Strength", "session_category": "Max Strength",
            "session_domain": "strength", "week_number": 2, "duration_weeks": 8,
            "deload_every_n_weeks": 4, "intensity": workload,
        },
    )
    assert rx.structure is not None
    assert project_exercises(rx.structure) == rx.exercises, "projection stayed lossless"
    return sum(b.sets or 0 for b in rx.structure if hasattr(b, "sets"))


def _state():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_prescriber_candidates import _state as state

    return state(max_strength=50.0)


def test_lowering_the_workload_never_increases_prescribed_sets() -> None:
    assert _sets_for("easy") < _sets_for("medium") < _sets_for("hard")


def test_the_workload_domains_are_the_ones_with_countable_sets() -> None:
    """A "set" added to a Zone-2 run would be noise dressed as a setting."""
    assert "running" not in INTENSITY_SET_DOMAINS
    assert {"strength", "hypertrophy", "power"} <= INTENSITY_SET_DOMAINS


# ── the deferred half, pinned ────────────────────────────────────────────────

def test_the_deload_still_scales_duration_and_that_is_deliberate() -> None:
    """Deload removing SETS is correct semantics, and must wait for v1 activation (8C).

    Measured on a 75-minute, 12-set session against a normal week:

        v0, today's deload (45 min, 12 sets)      0.53x   dose falls — right direction
        v0, 2.3-style deload (75 min, 7 sets)     1.85x   dose RISES when work is removed
        v1, 2.3-style deload                      0.56x   falls — right direction

    v0's density is minutes-per-set, so taking sets out of a fixed session makes it read as
    denser and the recorded dose nearly doubles on a recovery week. Correct prescription
    semantics would make the live model materially worse, so the flip is gated on activation.
    """
    normal = recommend_next_session(
        _state(), goal="Strength",
        block_context={
            "block_goal": "Strength", "session_category": "Max Strength",
            "session_domain": "strength", "week_number": 2, "duration_weeks": 8,
            "deload_every_n_weeks": 4,
        },
    )
    deload = recommend_next_session(
        _state(), goal="Strength",
        block_context={
            "block_goal": "Strength", "session_category": "Max Strength",
            "session_domain": "strength", "week_number": 4, "duration_weeks": 8,
            "deload_every_n_weeks": 4, "is_deload": True,
        },
    )

    assert deload.duration_min < normal.duration_min, "the deload still shortens the session"
    normal_sets = sum(e.sets or 0 for e in normal.exercises)
    deload_sets = sum(e.sets or 0 for e in deload.exercises)
    assert deload_sets == normal_sets, (
        "the deload does not yet remove sets — flipping it while production runs v0 would "
        "nearly double the recorded dose of a recovery week"
    )
