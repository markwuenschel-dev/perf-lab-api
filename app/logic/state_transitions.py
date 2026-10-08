"""The state transitions the live writers and the replay engine share (P3b-2).

Everything that decides what state row an event produces, beyond the operators themselves,
lives here: the order of operator and floor, the no-op test, the timestamp the result carries.
The live paths (``process_new_workout``, ``stage_observation``) and the tail replay call these
same functions with the same inputs, so they cannot disagree about the transition. This module
is part of the transition identity (``app.engine.transition_identity``): editing it changes
the digest, and rows written before the edit stop being replayable.

What is deliberately *not* here: the strength-decline machine (a DB-backed decision whose
outcome is captured per observation; only a passthrough is replayable) and anything that
writes. All functions are pure and never mutate their inputs.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.logic.state_update_v0 import (
    WorkoutOperatorLog,
    apply_benchmark_observation,
    capacity_increased,
    floor_capacity_at_prior,
    update_athlete_state,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose


def apply_workout_transition(
    prev: UnifiedStateVector,
    dose: StressDose,
    dt: timedelta,
    log: WorkoutOperatorLog,
    *,
    event_time: datetime,
) -> UnifiedStateVector:
    """The state after a workout: the operator's result, valid as of the workout's own time
    (identical to the engine's ``prev + dt``)."""
    new_state = update_athlete_state(prev, dose, dt, log)
    new_state.timestamp = event_time
    return new_state


@dataclass(frozen=True)
class BenchmarkOperatorInput:
    """What the benchmark operator reads. ``mappings`` are read by attribute only: ORM rows on
    the live path, ``MappingSnapshot`` on replay."""

    observed_at: datetime
    raw_value: float
    normalized_value: float | None
    score01: float | None
    better_direction: str
    observation_weight_used: float
    mappings: Sequence[Any]


def run_benchmark_operator(
    current: UnifiedStateVector, op: BenchmarkOperatorInput
) -> UnifiedStateVector:
    return apply_benchmark_observation(
        current,
        raw_value=op.raw_value,
        normalized_value=op.normalized_value,
        better_direction=op.better_direction,
        observation_weight=op.observation_weight_used,
        mappings=op.mappings,
        observed_at=op.observed_at,
        score01=op.score01,
    )


def floor_if_raised(
    current: UnifiedStateVector, candidate: UnifiedStateVector
) -> UnifiedStateVector | None:
    """The non-regressing handlers (``upward_lower_bound``, ``initialize_prior``): clamp every
    capacity axis up to its prior; if that raised nothing the observation writes no state."""
    floored = floor_capacity_at_prior(current, candidate)
    return floored if capacity_increased(current, floored) else None
