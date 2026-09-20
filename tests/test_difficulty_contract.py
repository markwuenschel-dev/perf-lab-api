"""The difficulty contract (phase 3.1) — declarations and detection, not behaviour.

Difficulty is not a dose dimension; it is a family-specific transformation OVER dose
dimensions. 3.1 writes that down — which dimensions exist, what each family may touch, and a
detector that reports what a transform actually moved — while the live prescription stays
byte-identical, because the only transform implemented is today's rule wrapped in the
interface.

These tests therefore check two different things:

* the contract says something meaningful and self-consistent, and
* wiring the live path through it changed nothing.
"""
import pytest

from app.logic.difficulty import (
    DIFFICULTIES,
    LEGACY_TRANSFORM,
    POLICIES,
    DifficultyDimension,
    Permission,
    dimensions_changed,
    violations,
)
from app.logic.planning import INTENSITY_CHOICES
from app.schemas.workout_structure import (
    ContinuousBlock,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    adjust_strength_sets,
)


def _squat(**overrides) -> StrengthBlock:
    base = {
        "exercise": "Back Squat", "sets": 4, "reps": "5", "percent_e1rm": 0.8,
        "rpe_target": 8.0, "rest_sec": 180,
    }
    base.update(overrides)
    return StrengthBlock(**base)


# ── the contract says something ──────────────────────────────────────────────

def test_difficulty_reuses_the_block_workload_vocabulary() -> None:
    """One vocabulary for easy/medium/hard, not two that can drift."""
    assert DIFFICULTIES == INTENSITY_CHOICES


def test_every_declared_family_states_a_rationale() -> None:
    """A permission without a reason is an assertion; the reason is what makes it reviewable."""
    for name, policy in POLICIES.items():
        assert policy.family == name
        assert len(policy.rationale) > 40, f"{name} has no real rationale"


def test_every_family_declares_every_dimension() -> None:
    for name, policy in POLICIES.items():
        for dimension in DifficultyDimension:
            assert isinstance(policy.permission(dimension), Permission), f"{name}/{dimension}"


def test_no_family_may_change_which_movements_appear() -> None:
    """Difficulty makes a session harder; it does not make it a different session."""
    for name, policy in POLICIES.items():
        assert policy.forbids(DifficultyDimension.EXERCISE_SELECTION), name


def test_max_velocity_sprint_cannot_manufacture_difficulty_from_recovery() -> None:
    """Cutting recovery lowers velocity — the one quality the session trains."""
    policy = POLICIES["max_velocity_sprint"]

    assert policy.forbids(DifficultyDimension.DENSITY)
    assert policy.forbids(DifficultyDimension.INTENSITY), "already maximal by definition"
    assert policy.permission(DifficultyDimension.VOLUME) is Permission.ALLOWED


def test_max_strength_and_hypertrophy_differ_in_what_difficulty_may_touch() -> None:
    """The whole reason a global rule is wrong: these two are not the same session."""
    assert POLICIES["max_strength"].forbids(DifficultyDimension.DENSITY)
    assert POLICIES["hypertrophy"].permission(DifficultyDimension.DENSITY) is (
        Permission.CONSTRAINED
    )


def test_easy_aerobic_hardens_by_duration_not_by_pace() -> None:
    policy = POLICIES["easy_aerobic"]

    assert policy.permission(DifficultyDimension.VOLUME) is Permission.ALLOWED
    assert policy.permission(DifficultyDimension.INTENSITY) is Permission.CONSTRAINED


def test_the_endurance_families_are_declared_without_transforms() -> None:
    """Declared now, implemented in phase 5 against real interval/continuous structure.

    Writing running transforms today would mean transforming legacy prose, and baking in
    assumptions that structure is about to make unnecessary.
    """
    for family in ("easy_aerobic", "threshold", "max_velocity_sprint"):
        assert family in POLICIES
    assert LEGACY_TRANSFORM.family == "legacy_set_step"


# ── the detector ─────────────────────────────────────────────────────────────

def test_a_volume_change_is_reported_as_volume_alone() -> None:
    before = [_squat(sets=4)]

    assert dimensions_changed(before, [_squat(sets=6)]) == {DifficultyDimension.VOLUME}


def test_a_hardening_that_also_raises_effort_is_caught() -> None:
    """The 3.3 invariant this exists for: volume-only must not move the RPE target."""
    before = [_squat(sets=4, rpe_target=8.0)]
    after = [_squat(sets=6, rpe_target=9.5)]

    assert dimensions_changed(before, after) == {
        DifficultyDimension.VOLUME,
        DifficultyDimension.EFFORT,
    }


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"percent_e1rm": 0.9}, DifficultyDimension.INTENSITY),
        ({"rir_target": 1.0}, DifficultyDimension.EFFORT),
        ({"rest_sec": 60}, DifficultyDimension.DENSITY),
        ({"exercise": "Front Squat"}, DifficultyDimension.EXERCISE_SELECTION),
    ],
)
def test_each_dimension_is_detected_independently(update, expected) -> None:
    assert dimensions_changed([_squat()], [_squat(**update)]) == {expected}


def test_adding_a_movement_counts_as_selection_not_volume() -> None:
    """Adding work by adding an exercise is a different act from adding a set."""
    before = [_squat()]
    after = [_squat(), _squat(exercise="Romanian Deadlift")]

    assert DifficultyDimension.EXERCISE_SELECTION in dimensions_changed(before, after)


def test_an_unchanged_structure_moves_nothing() -> None:
    structure = [_squat(), WarmupBlock(duration_sec=600)]

    assert dimensions_changed(structure, list(structure)) == set()


def test_the_detector_covers_the_blocks_phase_5_will_emit() -> None:
    """Dormant, but the detector must already understand them or 3.3 is blind to running."""
    intervals = [IntervalBlock(repetitions=6, work_duration_sec=180, recovery_duration_sec=120)]
    more_reps = [IntervalBlock(repetitions=8, work_duration_sec=180, recovery_duration_sec=120)]
    less_recovery = [
        IntervalBlock(repetitions=6, work_duration_sec=180, recovery_duration_sec=60)
    ]
    steady = [ContinuousBlock(duration_sec=1800, intensity_target=4.0, intensity_basis="zone")]
    faster = [ContinuousBlock(duration_sec=1800, intensity_target=5.0, intensity_basis="zone")]

    assert dimensions_changed(intervals, more_reps) == {DifficultyDimension.VOLUME}
    assert dimensions_changed(intervals, less_recovery) == {DifficultyDimension.DENSITY}
    assert dimensions_changed(steady, faster) == {DifficultyDimension.INTENSITY}


def test_violations_name_the_family_and_the_dimension() -> None:
    before = [_squat(rest_sec=180)]
    after = [_squat(rest_sec=60)]

    assert violations(before, after, POLICIES["max_strength"]) == [
        "max_strength must not change density"
    ]
    assert violations(before, after, POLICIES["strength"]) == [], "constrained is not forbidden"


# ── behaviour neutrality ─────────────────────────────────────────────────────

@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_the_legacy_transform_reproduces_the_previous_rule_exactly(level: str) -> None:
    """±1 working set, floored at one, nothing else — the rule the live path used before."""
    from app.logic.planning import intensity_set_delta

    structure = [_squat(sets=4), _squat(exercise="Romanian Deadlift", sets=3)]

    assert LEGACY_TRANSFORM.apply(structure, level) == adjust_strength_sets(
        structure, intensity_set_delta(level)
    )


def test_the_legacy_transform_moves_volume_and_nothing_else() -> None:
    structure = [_squat()]

    for level in DIFFICULTIES:
        after = LEGACY_TRANSFORM.apply(structure, level)
        moved = dimensions_changed(structure, after)
        assert moved <= {DifficultyDimension.VOLUME}, f"{level} moved {moved}"


def test_an_unknown_level_is_treated_as_medium() -> None:
    structure = [_squat()]

    assert LEGACY_TRANSFORM.apply(structure, "nonsense") == structure
