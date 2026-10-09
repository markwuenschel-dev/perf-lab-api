"""Fold a late workout or benchmark into the athlete's state, atomically (P3b-3).

A late event is one recorded record-only because it precedes the state head (P3a, P3-pre).
``fold_late_events`` plans the exact tail replay (``tail_replay_service``: proof first, then the
corrected head), and applies it in the caller's transaction:

* a **receipt** (``state_corrections``) and the events it introduces (``state_correction_events``),
* a **correction head**: a new ``athlete_states`` row at the old head's timestamp holding the
  replayed state (it wins by id, so every reader that takes the latest row sees it),
* the introduced events' **disposition**, ``record_only`` -> ``applied``.

All of it happens inside one SAVEPOINT. If planning refuses (``ReplayUnsupported``) or anything
else fails, the savepoint rolls back, the event stays record-only, and the refusal code is kept
on the event (``replay_refusal``) so a later repair can tell "never replayable" from "not yet".
A failure here can never lose the event or the athlete's workout; the safe state is the one
before this module existed.

What it never does: it does not commit (the caller owns the transaction), and it writes nothing
the live writers write for an event as a side effect (weak-point feedback, e1RM evidence, decline
candidates, shadow telemetry). The replay re-derives state only.

Behind ``APPLY_LATE_EVENTS`` (off by default); the callers check it.

Policy, provisional like the 48 h window it extends (P3 decision, fork 2):

* ``MAX_REPLAY_GAP``: the head may be at most this far past the earliest late event.
* ``MAX_REPLAY_EVENTS``: events replayed (tail + late). The oracle tests cover up to this size.
* ``MAX_BATCH``: late events folded together. Beyond it, or beyond the tail size, the answer
  is record-only, not an untested correction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.logic.tail_replay import ReplayUnsupported
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.state_correction import StateCorrection, StateCorrectionEvent
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.services.state_chain_lock import lock_athlete_chain
from app.services.tail_replay_service import NewEventRef, TailReplayPlan, plan_tail_replay

logger = logging.getLogger(__name__)

MAX_REPLAY_GAP = timedelta(hours=48)
MAX_REPLAY_EVENTS = 8
MAX_BATCH = 4


@dataclass(frozen=True)
class FoldOutcome:
    applied: bool
    refusal: str | None = None  # the stable code that kept the events record-only
    correction_id: int | None = None
    head: AthleteState | None = None  # the correction head, when applied


async def _event_row(db: AsyncSession, ref: NewEventRef) -> WorkoutLog | BenchmarkObservation:
    row = (
        await db.get(WorkoutLog, ref.event_id)
        if ref.kind == "workout"
        else await db.get(BenchmarkObservation, ref.event_id)
    )
    assert row is not None  # planned from these refs a moment ago, in this transaction
    return row


async def apply_plan(db: AsyncSession, user_id: int, plan: TailReplayPlan) -> AthleteState:
    """Write a plan: receipt, introduced events, correction head, dispositions. No commit."""
    await lock_athlete_chain(db, user_id)
    # The plan was proven against this head; refuse to write it over any other.
    latest = await AthleteContextRepository(db).get_latest_state(user_id)
    if latest is None or latest.id != plan.head_before_row_id:
        raise ReplayUnsupported("head_moved", f"head is {latest.id if latest else None}")

    receipt = StateCorrection(
        user_id=user_id,
        algorithm_version=plan.algorithm_version,
        transition_identity=plan.transition_identity,
        checkpoint_state_id=plan.checkpoint_row_id,
        head_before_state_id=plan.head_before_row_id,
        affected_from=plan.affected_from,
    )
    db.add(receipt)
    await db.flush()
    for ordinal, event in enumerate(plan.new_events):
        db.add(
            StateCorrectionEvent(
                correction_id=receipt.id,
                ordinal=ordinal,
                workout_log_id=event.event_id if event.kind == "workout" else None,
                observation_id=event.event_id if event.kind == "benchmark" else None,
            )
        )
    head = AthleteState(
        user_id=user_id,
        event_kind="correction",
        source_correction_id=receipt.id,
        predecessor_state_id=plan.head_before_row_id,
        transition_identity=plan.transition_identity,
        **athlete_state_kwargs_from_unified(plan.corrected_head),
    )
    db.add(head)
    for event in plan.new_events:
        row = await _event_row(db, NewEventRef(event.kind, event.event_id))
        row.state_disposition = "applied"
        row.state_disposition_reason = None
        row.replay_refusal = None
    await db.flush()
    return head


async def _owned_event_rows(
    db: AsyncSession, user_id: int, refs: list[NewEventRef]
) -> list[WorkoutLog | BenchmarkObservation] | None:
    """The events behind ``refs``, or None if any is unknown or belongs to another athlete.
    Checked before anything is written, so a bad reference can never cause a write to
    someone else's event."""
    rows: list[WorkoutLog | BenchmarkObservation] = []
    for ref in refs:
        if ref.kind == "workout":
            row: WorkoutLog | BenchmarkObservation | None = await db.get(WorkoutLog, ref.event_id)
        elif ref.kind == "benchmark":
            row = await db.get(BenchmarkObservation, ref.event_id)
        else:
            return None
        if row is None or row.user_id != user_id:
            return None
        rows.append(row)
    return rows


async def fold_late_events(
    db: AsyncSession, user_id: int, refs: list[NewEventRef]
) -> FoldOutcome:
    """Try to fold record-only ``refs`` into the state. Never raises for a refusal and never
    commits; see the module docstring."""
    await lock_athlete_chain(db, user_id)
    rows = await _owned_event_rows(db, user_id, refs)
    if rows is None:
        return FoldOutcome(applied=False, refusal="ownership")  # writes nothing, anywhere
    refusal: str | None = None
    head: AthleteState | None = None
    correction_id: int | None = None
    if not refs or len(refs) > MAX_BATCH:
        refusal = "batch_too_large" if refs else "no_new_events"
    else:
        try:
            async with db.begin_nested():
                plan = await plan_tail_replay(
                    db, user_id, refs, max_gap=MAX_REPLAY_GAP, max_events=MAX_REPLAY_EVENTS
                )
                head = await apply_plan(db, user_id, plan)
                correction_id = head.source_correction_id
        except ReplayUnsupported as exc:
            refusal = exc.code
        except Exception:
            # The savepoint has rolled back: the event stays record-only. Say so loudly, once.
            logger.exception("late-event fold failed for user %s; kept record-only", user_id)
            refusal = "internal_error"
    if refusal is not None:
        for row in rows:
            # A rolled-back savepoint expires what it touched: read the rows afresh. Only an
            # event that is actually waiting (record-only) carries a refusal; an applied one has
            # nothing to explain.
            await db.refresh(row)
            if row.state_disposition == "record_only":
                row.replay_refusal = refusal
        return FoldOutcome(applied=False, refusal=refusal)
    return FoldOutcome(applied=True, correction_id=correction_id, head=head)

