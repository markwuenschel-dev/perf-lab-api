"""Exact tail replay: re-run the events after a checkpoint through the live transitions (P3b-2).

A workout or benchmark that arrives after a later event was recorded (record-only) can be
folded in exactly by replaying everything after the last trustworthy state with the new event
in its chronological place. This module is the pure part: ordering, stepping, comparing. It
reads no database and writes nothing; ``app.services.tail_replay_service`` loads, validates
and proves.

Fail closed. Anything the replay cannot reproduce faithfully raises ``ReplayUnsupported``
with a stable ``code``; the caller keeps the event record-only. Nothing here is a best effort.

Ordering. A position is ``(timestamp, row_key, ordinal)``:

* an event that wrote its own state row keys on that row's id (arrival order);
* an event a correction introduced keys on that correction's head row id, then its ordinal in
  the correction's batch, so it sorts after every event that arrived before the correction;
* a new event keys after everything (``NEW_EVENT_ROW_KEY``), then its ordinal.

Events at the same timestamp are ordered by that key. An event with no key (a benchmark that
wrote no row) sharing a timestamp with another event is ambiguous: refused.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.logic.replay_inputs import REPLAY_INPUT_VERSION, operator_input_from_snapshot
from app.logic.state_transitions import (
    apply_workout_transition,
    floor_if_raised,
    run_benchmark_operator,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose

# Sorts after any row id: where a new event lands among equal timestamps (it arrived last).
NEW_EVENT_ROW_KEY = sys.maxsize

# Capacity axis whose observations the strength-decline machine judges. Its decision depends on
# the athlete's observation history (watermark, open candidates), which inserting an earlier
# event can change; replay can't re-decide it, so a tail containing one is not replayable.
DECLINE_AXIS_TARGET = ("capacity", "max_strength")

# The columns of a state row the transition determines. Everything else on the row is
# bookkeeping (links, kind, identity).
STATE_COLUMNS = (
    "timestamp", "c_met_aerobic", "c_nm_force", "c_struct", "b_met_anaerobic",
    "f_met_systemic", "f_nm_peripheral", "f_nm_central", "f_struct_damage",
    "s_struct_signal", "habit_strength", "skill_state", "engine_state",
)


class ReplayUnsupported(Exception):
    """The replay cannot be trusted for this input. ``code`` is stable and machine-readable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


EventKind = Literal["workout", "benchmark"]


@dataclass(frozen=True)
class ReplayEvent:
    kind: EventKind
    event_id: int
    timestamp: datetime  # naive UTC
    row_key: int | None  # the id of the state row that gives this event its place, if any
    ordinal: int
    replay_input: Mapping[str, Any]
    own_state_row_id: int | None = None  # the row this event wrote live
    is_new: bool = False

    @property
    def position(self) -> tuple[datetime, int, int]:
        if self.row_key is None:
            raise ReplayUnsupported("ambiguous_tie", f"{self.kind} {self.event_id} has no row key")
        return (self.timestamp, self.row_key, self.ordinal)


@dataclass(frozen=True)
class ReplayStep:
    event: ReplayEvent
    state: UnifiedStateVector  # the state after the event (unchanged if it wrote no row)
    wrote_row: bool


@dataclass(frozen=True)
class _ReplayLog:
    """A ``WorkoutOperatorLog`` built from a captured workout snapshot."""

    modality: str
    dominant_movement_pattern: str | None
    sleep_quality: float | None
    life_stress_inverse: float | None


def order_events(events: Sequence[ReplayEvent]) -> list[ReplayEvent]:
    """Chronological order with the tie rule above; refuses what it cannot order."""
    seen: set[tuple[str, int]] = set()
    for e in events:
        ident = (e.kind, e.event_id)
        if ident in seen:
            raise ReplayUnsupported("double_membership", f"{e.kind} {e.event_id} appears twice")
        seen.add(ident)
    by_time: dict[datetime, list[ReplayEvent]] = {}
    for e in events:
        by_time.setdefault(e.timestamp, []).append(e)
    for ts, group in by_time.items():
        if len(group) > 1 and any(e.row_key is None for e in group):
            raise ReplayUnsupported("ambiguous_tie", f"{len(group)} events at {ts.isoformat()}")
    ordered = sorted(events, key=lambda e: (e.timestamp, e.row_key if e.row_key is not None else -1, e.ordinal))
    positions = [(e.timestamp, e.row_key, e.ordinal) for e in ordered]
    if len(set(positions)) != len(positions):
        raise ReplayUnsupported("ambiguous_tie", "two events share a position")
    return ordered


def targets_decline_axis(snapshot: Mapping[str, Any]) -> bool:
    return any(
        (m["target_vector"], m["target_key"]) == DECLINE_AXIS_TARGET for m in snapshot["mappings"]
    )


def _workout_step(current: UnifiedStateVector, event: ReplayEvent) -> ReplayStep:
    ri = event.replay_input
    if ri.get("v") != REPLAY_INPUT_VERSION or ri.get("kind") != "workout":
        raise ReplayUnsupported("unknown_capture", f"workout {event.event_id}")
    try:
        dose = StressDose(**ri["dose"])
        log = _ReplayLog(
            modality=ri["modality"],
            dominant_movement_pattern=ri["dominant_movement_pattern"],
            sleep_quality=ri["sleep_quality"],
            life_stress_inverse=ri["life_stress_inverse"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayUnsupported("capture_invalid", f"workout {event.event_id}: {exc}") from exc
    dt = event.timestamp - current.timestamp
    if dt.total_seconds() < 0:
        raise ReplayUnsupported("negative_interval", f"workout {event.event_id}")
    new_state = apply_workout_transition(current, dose, dt, log, event_time=event.timestamp)
    return ReplayStep(event=event, state=new_state, wrote_row=True)


def _benchmark_step(current: UnifiedStateVector, event: ReplayEvent) -> ReplayStep:
    ri = event.replay_input
    if ri.get("v") != REPLAY_INPUT_VERSION or ri.get("kind") != "benchmark":
        raise ReplayUnsupported("unknown_capture", f"benchmark {event.event_id}")
    decline = ri.get("decline")
    if decline is not None and decline.get("intercepted"):
        raise ReplayUnsupported("decline_outcome", f"benchmark {event.event_id}")
    effect = ri.get("effect")
    if effect not in ("bidirectional_update", "initialize_prior"):
        raise ReplayUnsupported("unsupported_effect", f"benchmark {event.event_id}: {effect}")
    try:
        if targets_decline_axis(ri):
            raise ReplayUnsupported("decline_policy_engaged", f"benchmark {event.event_id}")
        op = operator_input_from_snapshot(ri)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayUnsupported("capture_invalid", f"benchmark {event.event_id}: {exc}") from exc
    if op.observed_at != event.timestamp:
        raise ReplayUnsupported("timestamp_mismatch", f"benchmark {event.event_id}")
    new_state: UnifiedStateVector | None = run_benchmark_operator(current, op)
    if effect == "initialize_prior":
        new_state = floor_if_raised(current, new_state)
    if new_state is None:
        return ReplayStep(event=event, state=current, wrote_row=False)
    return ReplayStep(event=event, state=new_state, wrote_row=True)


def replay(checkpoint: UnifiedStateVector, events: Sequence[ReplayEvent]) -> list[ReplayStep]:
    """Apply ``events`` (ordered by :func:`order_events`) to ``checkpoint``, one step each."""
    steps: list[ReplayStep] = []
    current = checkpoint
    for event in events:
        step = _workout_step(current, event) if event.kind == "workout" else _benchmark_step(current, event)
        steps.append(step)
        current = step.state
    return steps


def state_columns(state: UnifiedStateVector) -> dict[str, Any]:
    """The state row the live writers would store for ``state``."""
    kwargs = athlete_state_kwargs_from_unified(state)
    return {name: kwargs[name] for name in STATE_COLUMNS}


def row_columns(row: Any) -> dict[str, Any]:
    return {name: getattr(row, name) for name in STATE_COLUMNS}


def diff_columns(expected: Mapping[str, Any], stored: Mapping[str, Any]) -> list[str]:
    """Names of the columns that differ. Exact equality: the same code on the same inputs
    reproduces floats bit for bit, so there is no tolerance to hide drift in."""
    return [name for name in STATE_COLUMNS if expected[name] != stored[name]]


# --------------------------------------------------------------------------- #
# Staleness and checkpoints
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CorrectionMark:
    """What staleness needs of a correction: where it started and which row is its head."""

    head_row_id: int
    affected_from: datetime


def is_stale(row_id: int, row_timestamp: datetime, corrections: Sequence[CorrectionMark]) -> bool:
    """A row a later correction made out of date: stored before the correction's head, and
    after the earliest event it introduced (rows at or before that were computed correctly).
    Stale rows stay stored (nothing is deleted) but are never a checkpoint or a check."""
    return any(
        row_id < c.head_row_id and row_timestamp > c.affected_from for c in corrections
    )
