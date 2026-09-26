"""A running session is structured work; exercises[] is only its compatibility view (phase 5.3).

The acceptance gates for 5.3, as tests:

1. running templates produce interval / continuous blocks, never strength blocks;
2. ``project_exercises(structure)`` reproduces the athlete-visible exercise list exactly;
7. timed runs now have a calculated duration;
8. distance-only work stays unknown — no pace is invented to time it.

Gates 3-6 (log prefill, v0 dose, v1 shadow dose, state) hold because every one of them reads
only ``exercises[]`` (``state_service._seed_exercises_from_prescription``), which gate 2 pins.
The before/after proof over a 33-scenario grid is in the 5.3 commit message.

Re-derivation is the other half: exercises are edited after selection (loads, weak-point
tags, accessories), and every re-derivation must keep a run a run.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY, CandidateTemplate
from app.logic.exercise_slot import CatalogExercise
from app.logic.prescriber import (
    _select_exercises,
    _structure_for_selection,
    recommend_next_session,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.prescription import (
    ExercisePrescription,
    WorkoutPrescription,
    project_exercises,
    structure_from_exercises,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workout_structure import (
    ContinuousBlock,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
)

_RUNNING = [
    t for pool in ("running", "sprinting", "running_recovery") for t in GOAL_TEMPLATE_LIBRARY[pool]
]


def _healthy() -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState()
    t = TissueState()
    return UnifiedStateVector(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **sync_legacy_from_vectors(cx, f, t),
    )


def _prescribe(
    catalog: list[CatalogExercise], goal: str, kpi: dict[str, float], category: str | None
) -> WorkoutPrescription:
    block = (
        None if category is None
        else {"session_domain": "running", "session_category": category}
    )
    return recommend_next_session(
        _healthy(),
        goal=goal,  # type: ignore[arg-type]
        kpi_summary=kpi,
        catalog=catalog,
        block_context=block,
    )


_INTERVALS = ("5K", {"run_fatigue_factor": 20.0}, "Threshold Work")
_TEMPO = ("HalfMarathon", {}, "Threshold Work")
_Z2 = ("5K", {"run_fatigue_factor": 10.0}, "Aerobic Base")
_SPRINT = ("Sprinting", {}, None)


# ── gate 1 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("template", _RUNNING, ids=lambda t: t.branch_id)
def test_every_running_template_is_endurance_structured(
    template: CandidateTemplate, catalog_snapshot: list[CatalogExercise]
) -> None:
    """Every slot declares a work shape, and selection turns each into an endurance block.

    Per template, at the selection seam — run_speed_endurance never wins a scenario the
    end-to-end tests below can reach cheaply, and it still must not be strength-shaped.
    """
    assert all(slot.endurance is not None for slot in template.exercise_slots)

    selection = _select_exercises(template.exercise_slots, None, catalog_snapshot)
    blocks = _structure_for_selection(selection)

    assert blocks and all(isinstance(b, IntervalBlock | ContinuousBlock) for b in blocks)
    assert project_exercises(blocks) == selection.exercises


@pytest.mark.parametrize(
    ("scenario", "kinds"),
    [
        (_Z2, ["continuous"]),
        (("5K", {"run_fatigue_factor": 20.0}, "Aerobic Base"), ["continuous"]),
        (_TEMPO, ["continuous"]),
        (_INTERVALS, ["interval"]),
        (_SPRINT, ["interval", "interval"]),
    ],
    ids=["z2", "z2_threshold", "tempo", "intervals", "sprint"],
)
def test_the_prescriber_emits_endurance_blocks_for_running(
    catalog_snapshot: list[CatalogExercise],
    scenario: tuple[str, dict[str, float], str | None],
    kinds: list[str],
) -> None:
    rx = _prescribe(catalog_snapshot, *scenario)

    assert rx.structure is not None
    assert [b.kind for b in rx.structure] == kinds


# ── gate 2 ───────────────────────────────────────────────────────────────────


def test_the_interval_session_projects_to_what_the_athlete_always_saw(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    """Typed internally as 4 x 300 s / 120 s @ RPE 8; displayed exactly as before."""
    rx = _prescribe(catalog_snapshot, *_INTERVALS)

    assert rx.structure is not None
    (block,) = rx.structure
    assert isinstance(block, IntervalBlock)
    assert (block.repetitions, block.work_duration_sec, block.recovery_duration_sec) == (
        4, 300, 120,
    )
    assert (block.intensity_basis, block.intensity_target) == ("rpe", 8.0)
    (ex,) = rx.exercises
    assert (ex.name, ex.sets, ex.reps) == (
        "Threshold Tempo Run", 4, "5 min @ threshold pace (RPE 8) / 2 min easy",
    )
    assert project_exercises(rx.structure) == rx.exercises


@pytest.mark.parametrize("template", _RUNNING, ids=lambda t: t.branch_id)
def test_an_interval_repeats_as_many_times_as_it_displays(template: CandidateTemplate) -> None:
    """The typed count and the legacy text must not tell the athlete two different numbers."""
    for slot in template.exercise_slots:
        if isinstance(slot.endurance, IntervalBlock):
            assert slot.endurance.repetitions == int(slot.sets), slot
        else:
            assert slot.sets == "1", slot


# ── gates 7 and 8 ────────────────────────────────────────────────────────────


def test_timed_runs_have_a_calculated_duration(catalog_snapshot: list[CatalogExercise]) -> None:
    intervals = _prescribe(catalog_snapshot, *_INTERVALS)
    tempo = _prescribe(catalog_snapshot, *_TEMPO)

    # 4 x 300 s work + 3 x 120 s recovery (none after the last rep) = 1560 s.
    assert intervals.calculated_duration_min == 26.0
    assert tempo.calculated_duration_min == 20.0


def test_distance_only_and_ranged_work_stays_unknown(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    """No pace is invented to time a sprint, and no midpoint is picked for "30-40 min"."""
    sprint = _prescribe(catalog_snapshot, *_SPRINT)
    z2 = _prescribe(catalog_snapshot, *_Z2)

    assert sprint.calculated_duration_min is None
    assert sprint.duration_estimate is not None
    assert all("distance-only" in c for c in sprint.duration_estimate.unknown_components)
    assert z2.calculated_duration_min is None


# ── re-derivation keeps a run a run ──────────────────────────────────────────


def _interval(**kw: object) -> IntervalBlock:
    base: dict[str, object] = {
        "activity": "Tempo Run",
        "display_sets": 4,
        "display_reps": "5 min",
        "repetitions": 4,
        "work_duration_sec": 300,
    }
    return IntervalBlock(**(base | kw))  # type: ignore[arg-type]


def _run(**kw: object) -> ExercisePrescription:
    return ExercisePrescription(name="Tempo Run", sets=4, reps="5 min", **kw)  # type: ignore[arg-type]


def test_rederiving_keeps_the_work_shape_and_takes_the_edited_exercise() -> None:
    """The weak-point enricher edits tags after selection; the block must carry the new ones."""
    edited = [_run(weak_point_tags=["lactate_threshold"])]

    (block,) = structure_from_exercises(edited, [_interval()])

    assert isinstance(block, IntervalBlock)
    assert block.work_duration_sec == 300
    assert block.weak_point_tags == ["lactate_threshold"]
    assert project_exercises([block]) == edited


def test_a_different_exercise_at_that_position_is_not_given_the_run_shape() -> None:
    squat = ExercisePrescription(name="Back Squat", sets=4, reps="5")

    (block,) = structure_from_exercises([squat], [_interval()])

    assert isinstance(block, StrengthBlock)


def test_an_exercise_carrying_a_load_stays_strength_shaped() -> None:
    """An endurance block has nowhere to put kilograms; dropping them would lose data."""
    loaded = _run(prescribed_load_kg=20.0)

    (block,) = structure_from_exercises([loaded], [_interval()])

    assert isinstance(block, StrengthBlock)
    assert project_exercises([block]) == [loaded]


def test_appended_accessories_become_strength_blocks_after_the_run() -> None:
    accessory = ExercisePrescription(name="Calf Raise", sets=3, reps="15")

    blocks = structure_from_exercises([_run(), accessory], [_interval()])

    assert [b.kind for b in blocks] == ["interval", "strength"]


def test_a_non_projecting_block_keeps_its_place() -> None:
    blocks = structure_from_exercises([_run()], [WarmupBlock(duration_sec=600), _interval()])

    assert [b.kind for b in blocks] == ["warmup", "interval"]


def test_without_a_previous_structure_nothing_changes_from_before() -> None:
    squat = [ExercisePrescription(name="Back Squat", sets=5, reps="5")]

    assert [b.kind for b in structure_from_exercises(squat)] == ["strength"]


def test_reattaching_structure_keeps_the_run(catalog_snapshot: list[CatalogExercise]) -> None:
    """The finalize / prescriber seam: re-attaching structure must not flatten the session,
    and what is persisted must read back as one consistent prescription."""
    rx = _prescribe(catalog_snapshot, *_INTERVALS)
    again = rx.with_structure()

    assert again.structure == rx.structure
    WorkoutPrescription.model_validate(again.to_prescribed_content())
