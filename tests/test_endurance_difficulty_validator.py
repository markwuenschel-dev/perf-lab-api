"""The difficulty validator, exercised on endurance structure (phase 5.5, deferred).

5.5 — real endurance difficulty transforms — is DEFERRED pending calibration evidence. The
lever each family may pull is already decided (``difficulty.POLICIES``): easy aerobic moves
duration, threshold moves accumulated work, a sprint session moves the number of quality
repetitions. How FAR each moves is not decided, and no universal progression constant exists
to borrow: endurance programming varies repetitions, duration, recovery and intensity by
session type, athlete and phase (Casado et al. 2022; Hofmann & Tschakert 2017; Tønnessen et
al. 2024; Haugen et al. 2022; Buchheit & Laursen 2013).

So nothing here is a policy. The transforms below are SYNTHETIC and live only in this file;
their magnitudes (one repetition, five minutes, thirty seconds) are arbitrary and prove
nothing about physiology. What they prove is that ``validate_transform`` reads interval and
continuous blocks correctly — until phase 5.3 it had only ever seen strength transforms.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY, CandidateTemplate
from app.logic.difficulty import (
    CONSTRAINTS,
    POLICIES,
    Difficulty,
    DifficultyDimension,
    dimensions_changed,
    validate_transform,
)
from app.logic.difficulty_strength import CANDIDATE_TRANSFORMS
from app.logic.exercise_slot import CatalogExercise
from app.logic.prescriber import _select_exercises, _structure_for_selection
from app.schemas.prescription import project_exercises
from app.schemas.workout_structure import (
    ContinuousBlock,
    IntervalBlock,
    WarmupBlock,
    WorkoutBlock,
    WorkoutStructure,
    apply_volume_modifier,
)

ENDURANCE_FAMILIES = ("easy_aerobic", "threshold", "max_velocity_sprint")
_VOLUME = frozenset({DifficultyDimension.VOLUME})
_INTENSITY = frozenset({DifficultyDimension.INTENSITY})
_DENSITY = frozenset({DifficultyDimension.DENSITY})
_RUNNING = [
    t for pool in ("running", "sprinting", "running_recovery") for t in GOAL_TEMPLATE_LIBRARY[pool]
]

Edit = Callable[[IntervalBlock | ContinuousBlock, int], IntervalBlock | ContinuousBlock]


@dataclass(frozen=True)
class _SyntheticTransform:
    """A test-only DifficultyTransform: one edit per endurance block, signed by level."""

    family: str
    declared_dimensions: frozenset[DifficultyDimension]
    edit: Edit

    def apply(
        self,
        structure: WorkoutStructure,
        level: Difficulty,
        athlete_context: dict[str, object] | None = None,
    ) -> WorkoutStructure:
        sign = {"easy": -1, "hard": 1}.get(level, 0)
        if sign == 0:
            return list(structure)
        return [
            self.edit(b, sign) if isinstance(b, IntervalBlock | ContinuousBlock) else b
            for b in structure
        ]


def _reps(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
    assert isinstance(b, IntervalBlock) and b.repetitions is not None
    n = b.repetitions + sign
    return b.model_copy(update={"repetitions": n, "display_sets": n})


def _duration(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
    assert isinstance(b, ContinuousBlock) and b.duration_sec is not None
    return b.model_copy(update={"duration_sec": b.duration_sec + sign * 300})


def _work_time(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
    assert isinstance(b, IntervalBlock) and b.work_duration_sec is not None
    return b.model_copy(update={"work_duration_sec": b.work_duration_sec + sign * 30})


def _recovery(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
    """Harder = shorter recovery."""
    assert isinstance(b, IntervalBlock)
    return b.model_copy(update={"recovery_duration_sec": (b.recovery_duration_sec or 120) - sign * 30})


def _intensity(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
    return b.model_copy(update={"intensity_target": (b.intensity_target or 5.0) + sign})


# ── session shapes, as phase 5.3 emits them ──────────────────────────────────


def _easy_run() -> WorkoutStructure:
    """z2 as authored has no duration ("30-40 min" is a range), so give this one 40 min."""
    return [
        WarmupBlock(duration_sec=300),
        ContinuousBlock(
            activity="Easy Run", display_sets=1, display_reps="40 min conversational pace",
            duration_sec=2400, intensity_basis="zone", intensity_target=2.0,
        ),
    ]


def _threshold_intervals() -> WorkoutStructure:
    return [
        IntervalBlock(
            activity="Threshold Tempo Run", display_sets=4,
            display_reps="5 min @ threshold pace (RPE 8) / 2 min easy",
            repetitions=4, work_duration_sec=300, recovery_duration_sec=120,
            recovery_type="easy", intensity_basis="rpe", intensity_target=8.0,
        )
    ]


def _sprints() -> WorkoutStructure:
    return [
        IntervalBlock(activity="Flying Sprint", display_sets=3, display_reps="30 m",
                      repetitions=3, work_distance_m=30.0, recovery_duration_sec=180),
        IntervalBlock(activity="Acceleration", display_sets=4, display_reps="20 m",
                      repetitions=4, work_distance_m=20.0, recovery_duration_sec=120),
    ]


_SHAPES: dict[str, Callable[[], WorkoutStructure]] = {
    "easy_aerobic": _easy_run,
    "threshold": _threshold_intervals,
    "max_velocity_sprint": _sprints,
}


def _problems(family: str, transform: _SyntheticTransform, level: str = "hard") -> list[str]:
    before = _SHAPES[family]()
    return validate_transform(
        before, transform.apply(before, level),
        family=family, declared=transform.declared_dimensions,
    )


# ── accepted: the lever each family owns ─────────────────────────────────────


@pytest.mark.parametrize("level", ["easy", "hard"])
@pytest.mark.parametrize(
    ("family", "edit"),
    [
        ("easy_aerobic", _duration),
        ("threshold", _reps),
        ("threshold", _work_time),
        ("max_velocity_sprint", _reps),
    ],
    ids=["easy_aerobic_duration", "threshold_reps", "threshold_work_time", "sprint_reps"],
)
def test_the_lever_each_family_owns_is_accepted(family: str, edit: Edit, level: str) -> None:
    transform = _SyntheticTransform(family, _VOLUME, edit)

    assert _problems(family, transform, level) == []


@pytest.mark.parametrize("family", ENDURANCE_FAMILIES)
def test_medium_is_identity(family: str) -> None:
    transform = _SyntheticTransform(family, _VOLUME, _intensity)
    before = _SHAPES[family]()

    assert transform.apply(before, "medium") == before


# ── rejected: each for its own stated reason ─────────────────────────────────


@pytest.mark.parametrize(
    ("family", "edit", "declared", "reason"),
    [
        ("easy_aerobic", _intensity, _INTENSITY,
         "easy_aerobic moved intensity, which is constrained with no stated bound"),
        ("threshold", _recovery, _DENSITY,
         "threshold moved density, which is constrained with no stated bound"),
        ("threshold", _intensity, _INTENSITY,
         "threshold moved intensity, which is constrained with no stated bound"),
        ("max_velocity_sprint", _intensity, _INTENSITY,
         "max_velocity_sprint must not change intensity"),
        ("max_velocity_sprint", _recovery, _DENSITY,
         "max_velocity_sprint must not change density"),
    ],
    ids=["easy_intensity", "threshold_recovery", "threshold_intensity",
         "sprint_intensity", "sprint_recovery"],
)
def test_what_each_family_does_not_own_is_rejected(
    family: str, edit: Edit, declared: frozenset[DifficultyDimension], reason: str
) -> None:
    problems = _problems(family, _SyntheticTransform(family, declared, edit))

    assert len(problems) == 1 and problems[0].startswith(reason), problems


def test_a_volume_transform_that_also_speeds_up_is_two_changes_not_one() -> None:
    """Intent: declared volume-only, moved intensity too."""
    def more_and_faster(b: IntervalBlock | ContinuousBlock, sign: int) -> IntervalBlock | ContinuousBlock:
        return _intensity(_duration(b, sign), sign)

    problems = _problems("easy_aerobic", _SyntheticTransform("easy_aerobic", _VOLUME, more_and_faster))

    assert problems == [
        "easy_aerobic moved intensity, which this transform did not declare (declared: volume)"
    ]


# ── the fields phase 5.3 added must be visible to the detector ───────────────


@pytest.mark.parametrize(
    ("update", "dimension"),
    [
        ({"activity": "Easy Run"}, DifficultyDimension.EXERCISE_SELECTION),
        ({"rpe_cap": 9.5}, DifficultyDimension.EFFORT),
        ({"recovery_after_last_rep": True}, DifficultyDimension.DENSITY),
        ({"quality_stop": "stop when pace drops 5%"}, DifficultyDimension.VOLUME),
    ],
    ids=["activity", "rpe_cap", "recovery_after_last_rep", "quality_stop"],
)
def test_every_interval_field_that_changes_the_session_is_classified(
    update: dict[str, object], dimension: DifficultyDimension
) -> None:
    before = _threshold_intervals()
    after: WorkoutStructure = [before[0].model_copy(update=update)]

    assert dimensions_changed(before, after) == {dimension}


@pytest.mark.parametrize(
    ("update", "dimension"),
    [
        ({"activity": "Tempo Run"}, DifficultyDimension.EXERCISE_SELECTION),
        ({"rpe_cap": 7.0}, DifficultyDimension.EFFORT),
    ],
    ids=["activity", "rpe_cap"],
)
def test_every_continuous_field_that_changes_the_session_is_classified(
    update: dict[str, object], dimension: DifficultyDimension
) -> None:
    before = _easy_run()
    after: WorkoutStructure = [before[0], before[1].model_copy(update=update)]

    assert dimensions_changed(before, after) == {dimension}


@pytest.mark.parametrize("template", _RUNNING, ids=lambda t: t.branch_id)
def test_swapping_the_run_on_a_real_template_is_selection_not_nothing(
    template: CandidateTemplate, catalog_snapshot: list[CatalogExercise]
) -> None:
    """Swapping one run for another must be an exercise-selection change, which every
    endurance family forbids. On the structure the prescriber actually emits."""
    before = _structure_for_selection(
        _select_exercises(template.exercise_slots, None, catalog_snapshot)
    )
    swapped: list[WorkoutBlock] = [b.model_copy(update={"activity": "Walk"}) for b in before]

    assert dimensions_changed(before, swapped) == {DifficultyDimension.EXERCISE_SELECTION}
    for family in ENDURANCE_FAMILIES:
        assert f"{family} must not change exercise_selection" in validate_transform(
            before, swapped, family=family
        )


# ── what the athlete sees must follow a volume change ────────────────────────


def test_scaling_interval_repetitions_keeps_the_displayed_count_in_step() -> None:
    """Otherwise the typed session says 6 reps while the athlete is shown 4."""
    (scaled,) = apply_volume_modifier(_threshold_intervals(), 1.5)

    assert isinstance(scaled, IntervalBlock)
    assert scaled.repetitions == scaled.display_sets == 6
    assert project_exercises([scaled])[0].sets == 6


# ── deferral, pinned ─────────────────────────────────────────────────────────


def test_no_endurance_magnitude_policy_exists_yet() -> None:
    """5.5 is deferred: no endurance bound and no endurance transform. When one is added it
    must arrive with its evidence (the phase 3.4 rule), and this test is where that is noticed."""
    assert all(family not in ENDURANCE_FAMILIES for family, _ in CONSTRAINTS)
    assert not set(CANDIDATE_TRANSFORMS) & set(ENDURANCE_FAMILIES)
    assert set(ENDURANCE_FAMILIES) <= set(POLICIES)
