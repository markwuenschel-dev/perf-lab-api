"""Every reader of a workout structure handles every block kind, or refuses (phase 6.0).

A reader that dispatches on block kind with a silent fallthrough gives a wrong answer for a
kind it has never heard of: ``calculate_duration`` used to add 0 s for it and still report the
estimate as complete. Phase 6 adds a block kind, so before it does, every reader is exhaustive
(``assert_never``, which pyright checks statically), and these tests hold that at runtime:

* each kind in the ``WorkoutBlock`` union has a sample here, so a new kind cannot be added
  without being run through every reader;
* a block outside the union makes every reader raise instead of guessing.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, get_args

import pytest

from app.logic.difficulty import dimensions_changed
from app.logic.dose_engine_v1 import prescribed_timed_work_seconds
from app.schemas.prescription import project_exercises, structure_from_exercises
from app.schemas.workout_structure import (
    CircuitBlock,
    CircuitStation,
    ContinuousBlock,
    CooldownBlock,
    FixedRoundsScheme,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    WorkoutBlock,
    WorkoutStructure,
    _Block,  # pyright: ignore[reportPrivateUsage]
    apply_volume_modifier,
    calculate_duration,
)

SAMPLES: dict[type, object] = {
    StrengthBlock: StrengthBlock(exercise="Back Squat", sets=3, reps="5", rest_sec=180),
    IntervalBlock: IntervalBlock(activity="Run", repetitions=4, work_duration_sec=300),
    ContinuousBlock: ContinuousBlock(activity="Run", duration_sec=1800),
    CircuitBlock: CircuitBlock(
        stations=[CircuitStation(exercise="Wall Ball", reps=20)],
        scheme=FixedRoundsScheme(rounds=3),
    ),
    WarmupBlock: WarmupBlock(duration_sec=600),
    CooldownBlock: CooldownBlock(duration_sec=300),
}

READERS: dict[str, Callable[[WorkoutStructure], object]] = {
    "calculate_duration": calculate_duration,
    "apply_volume_modifier": lambda s: apply_volume_modifier(s, 0.5),
    "project_exercises": project_exercises,
    "structure_from_exercises": lambda s: structure_from_exercises(project_exercises(s), s),
    "difficulty_signature": lambda s: dimensions_changed(s, s),
    "dose_v1_timed_work": prescribed_timed_work_seconds,
}


def _union_members() -> set[type]:
    (union,) = get_args(WorkoutBlock)[:1]
    return set(get_args(union))


def test_every_block_kind_has_a_sample() -> None:
    assert set(SAMPLES) == _union_members()


_KINDS = sorted(SAMPLES, key=lambda c: c.__name__)


@pytest.mark.parametrize("reader", sorted(READERS))
@pytest.mark.parametrize("kind", _KINDS, ids=[c.__name__ for c in _KINDS])
def test_every_reader_handles_every_kind(reader: str, kind: type) -> None:
    assert READERS[reader]([SAMPLES[kind]]) is not None  # type: ignore[list-item]


class _UnknownBlock(_Block):
    kind: Literal["unknown"] = "unknown"


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_kind_outside_the_union_is_refused(reader: str) -> None:
    with pytest.raises(AssertionError):
        READERS[reader]([_UnknownBlock()])  # type: ignore[list-item]
