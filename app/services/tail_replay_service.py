"""Plan an exact tail replay, and prove it can be trusted first (P3b-2). Read-only.

Given late events (recorded record-only), this loads the athlete's state history, rebuilds the
state after the last trustworthy checkpoint from the *captured* inputs, and refuses unless that
rebuild reproduces what is stored. Only then does it replay again with the late events in their
chronological place and return the corrected head. Nothing is written: applying the plan
atomically is P3b-3.

The precondition is the caller's: hold ``lock_athlete_chain`` for the athlete, in the
transaction that will apply the plan, so the history cannot move between the proof and the write.

Fail closed. Every doubt is a ``ReplayUnsupported`` with a stable code, and the event stays
record-only. What it refuses, and why:

* ``no_state`` / ``no_checkpoint`` / ``untrusted_checkpoint``: no state at or before the earliest
  late event, or only rows written before capture existed (no event kind), which no one can
  vouch for.
* ``unclassified_state_write``: a row after the checkpoint that is not a workout, benchmark or
  correction (a repair, an unlabelled row, a mid-history baseline).
* ``ownership`` / ``lineage``: a row or event of another athlete, a row whose source link does
  not match its kind, or a predecessor that is not the previous row.
* ``identity_mismatch`` / ``registry_inconsistent``: the stored rows were not produced by the code
  running now, or the identity registry disagrees with itself.
* ``no_capture`` / ``capture_invalid`` / ``unknown_capture``: an event without (or with an
  unreadable) replay input.
* ``decline_outcome`` / ``decline_policy_engaged``: a benchmark the strength-decline machine
  judged or could re-judge. Its code is outside the transition identity.
* ``event_without_row`` / ``row_flag_mismatch`` / ``disposition_conflict`` / ``double_membership``:
  the event tables and the state rows disagree about what was applied. An applied event that
  wrote no row has no place in the order, so it is refused (the only benchmarks that write no
  row are initial priors and decline-held readings, both unsupported).
* ``initializer_tail``: an initial prior is in the tail; see ``tail_replay``.
* ``ambiguous_tie``: two events share one position and the order cannot be recovered.
* ``reconstruction_mismatch``: the proof failed: replaying the stored events from the checkpoint
  did not reproduce a stored row, which means something the capture does not cover changed it.
* ``not_late`` / ``unsupported_reason`` / ``already_introduced`` / ``not_record_only``: the new
  event is not a record-only event that precedes the head.
* ``window_exceeded`` / ``tail_too_long``: the caller's policy limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.engine_state_codec import EngineStateDecodeError
from app.engine.state_loading import unified_from_athlete_row_strict
from app.engine.transition_identity import current_identity, identity_from
from app.logic.tail_replay import (
    NEW_EVENT_ROW_KEY,
    CorrectionMark,
    ReplayEvent,
    ReplayStep,
    ReplayUnsupported,
    diff_columns,
    is_stale,
    order_events,
    replay,
    row_columns,
    state_columns,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.engine_transition_identity import EngineTransitionIdentity
from app.models.state_correction import StateCorrection, StateCorrectionEvent
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.state import UnifiedStateVector

ALGORITHM_VERSION = "tail-replay-v1"

_TAIL_KINDS = ("workout", "benchmark", "correction")
_CHECKPOINT_KINDS = ("baseline", "workout", "benchmark", "correction")
_LOOKBACK_BATCH = 20


@dataclass(frozen=True)
class NewEventRef:
    kind: str  # "workout" | "benchmark"
    event_id: int


@dataclass(frozen=True)
class TailReplayPlan:
    user_id: int
    algorithm_version: str
    transition_identity: str
    checkpoint_row_id: int
    head_before_row_id: int
    affected_from: datetime  # the earliest new event's timestamp
    gap: timedelta  # head timestamp - affected_from
    members: tuple[ReplayEvent, ...]  # existing events after the checkpoint, in order
    new_events: tuple[ReplayEvent, ...]
    steps: tuple[ReplayStep, ...]  # the replay with the new events in place
    corrected_head: UnifiedStateVector
    rows_proven: int  # stored rows the reconstruction reproduced exactly


def _key(row: AthleteState) -> tuple[datetime, int]:
    return (row.timestamp, row.id)


async def _load_corrections(
    db: AsyncSession, user_id: int
) -> tuple[list[StateCorrection], dict[int, list[StateCorrectionEvent]], dict[int, AthleteState]]:
    corrections = list(
        (await db.execute(select(StateCorrection).where(StateCorrection.user_id == user_id))).scalars()
    )
    ids = [c.id for c in corrections]
    events: dict[int, list[StateCorrectionEvent]] = {c.id: [] for c in corrections}
    heads: dict[int, AthleteState] = {}
    if ids:
        for e in (
            await db.execute(
                select(StateCorrectionEvent)
                .where(StateCorrectionEvent.correction_id.in_(ids))
                .order_by(StateCorrectionEvent.correction_id, StateCorrectionEvent.ordinal)
            )
        ).scalars():
            events[e.correction_id].append(e)
        for row in await AthleteContextRepository(db).list_states_by_correction_ids(ids):
            assert row.source_correction_id is not None
            heads[row.source_correction_id] = row
    for c in corrections:
        head_row = heads.get(c.id)
        if head_row is None or head_row.user_id != user_id or head_row.event_kind != "correction":
            raise ReplayUnsupported("lineage", f"correction {c.id} has no correction head row")
        await _validate_receipt(db, user_id, c, events[c.id], head_row)
    return corrections, events, heads


async def _validate_receipt(
    db: AsyncSession,
    user_id: int,
    c: StateCorrection,
    events: list[StateCorrectionEvent],
    head_row: AthleteState,
) -> None:
    """A receipt is evidence only if everything it points at belongs to this athlete and agrees
    with the correction head it produced. Checked for every receipt before any is used."""
    checkpoint = await db.get(AthleteState, c.checkpoint_state_id)
    before = await db.get(AthleteState, c.head_before_state_id)
    if (
        checkpoint is None or before is None
        or checkpoint.user_id != user_id or before.user_id != user_id
    ):
        raise ReplayUnsupported("ownership", f"correction {c.id} references another athlete's state")
    if (
        head_row.predecessor_state_id != before.id
        or head_row.transition_identity != c.transition_identity
        or head_row.timestamp != before.timestamp
        or head_row.id <= before.id
    ):
        raise ReplayUnsupported("lineage", f"correction {c.id} disagrees with its head row {head_row.id}")
    if _key(checkpoint) >= _key(before) or checkpoint.timestamp > c.affected_from:
        raise ReplayUnsupported("lineage", f"correction {c.id} has an impossible checkpoint")
    if [e.ordinal for e in events] != list(range(len(events))) or not events:
        raise ReplayUnsupported("lineage", f"correction {c.id} has a gapped or empty event list")
    times: list[datetime] = []
    for ce in events:
        if (ce.workout_log_id is None) == (ce.observation_id is None):
            raise ReplayUnsupported("lineage", f"correction {c.id} event {ce.id}")
        if ce.workout_log_id is not None:
            w = await db.get(WorkoutLog, ce.workout_log_id)
            if w is None or w.user_id != user_id:
                raise ReplayUnsupported("ownership", f"correction {c.id}: workout {ce.workout_log_id}")
            times.append(w.session_timestamp)
        else:
            assert ce.observation_id is not None
            o = await db.get(BenchmarkObservation, ce.observation_id)
            if o is None or o.user_id != user_id:
                raise ReplayUnsupported("ownership", f"correction {c.id}: observation {ce.observation_id}")
            times.append(o.observed_at)
    if min(times) != c.affected_from:
        raise ReplayUnsupported("lineage", f"correction {c.id} affected_from is not its earliest event")


async def _select_checkpoint(
    db: AsyncSession, user_id: int, t_min: datetime, marks: list[CorrectionMark]
) -> AthleteState:
    """The latest non-stale row at or before ``t_min``. A row at exactly ``t_min`` counts: a
    new event arrived after it, so it comes first."""
    offset = 0
    while True:
        batch = await AthleteContextRepository(db).list_states_at_or_before(
            user_id, t_min, limit=_LOOKBACK_BATCH, offset=offset
        )
        if not batch:
            raise ReplayUnsupported("no_checkpoint", f"no state at or before {t_min.isoformat()}")
        for row in batch:
            if not is_stale(row.id, row.timestamp, marks):
                if row.event_kind not in _CHECKPOINT_KINDS:
                    raise ReplayUnsupported(
                        "untrusted_checkpoint", f"row {row.id} has event kind {row.event_kind!r}"
                    )
                return row
        offset += _LOOKBACK_BATCH


async def _registry_ok(db: AsyncSession) -> str:
    identity = current_identity()
    registered = await db.get(EngineTransitionIdentity, identity.digest)
    if registered is None:
        raise ReplayUnsupported("registry_inconsistent", "the running identity is not registered")
    if (
        identity_from(registered.components).digest != registered.digest
        or registered.components != identity.components
    ):
        raise ReplayUnsupported("registry_inconsistent", "stored components do not match the digest")
    return identity.digest


def _check_rows(
    ck: AthleteState, rows: list[AthleteState], marks: list[CorrectionMark], digest: str
) -> None:
    # Rows are loaded by athlete, so their ownership is the query's; events' is checked in _members.
    previous = ck
    for row in rows:
        if row.event_kind not in _TAIL_KINDS:
            raise ReplayUnsupported(
                "unclassified_state_write", f"row {row.id} has event kind {row.event_kind!r}"
            )
        links = (
            row.source_workout_log_id is not None,
            row.source_observation_id is not None,
            row.source_correction_id is not None,
        )
        expected = {
            "workout": (True, False, False),
            "benchmark": (False, True, False),
            "correction": (False, False, True),
        }[row.event_kind]
        if links != expected:
            raise ReplayUnsupported("lineage", f"row {row.id} source links do not match {row.event_kind}")
        if row.predecessor_state_id != previous.id:
            raise ReplayUnsupported(
                "lineage", f"row {row.id} predecessor {row.predecessor_state_id} != {previous.id}"
            )
        if not is_stale(row.id, row.timestamp, marks) and row.transition_identity != digest:
            raise ReplayUnsupported("identity_mismatch", f"row {row.id}")
        previous = row


def _workout_event(
    w: WorkoutLog, *, row_key: int, ordinal: int, own: AthleteState | None, is_new: bool
) -> ReplayEvent:
    if w.replay_input is None:
        raise ReplayUnsupported("no_capture", f"workout {w.id}")
    return ReplayEvent(
        kind="workout", event_id=w.id, timestamp=w.session_timestamp, row_key=row_key,
        ordinal=ordinal, replay_input=w.replay_input,
        own_state_row_id=own.id if own is not None else None, is_new=is_new,
    )


def _benchmark_event(
    o: BenchmarkObservation, *, row_key: int, ordinal: int, own: AthleteState | None,
    is_new: bool,
) -> ReplayEvent:
    if o.replay_input is None:
        raise ReplayUnsupported("no_capture", f"observation {o.id}")
    return ReplayEvent(
        kind="benchmark", event_id=o.id, timestamp=o.observed_at, row_key=row_key,
        ordinal=ordinal, replay_input=o.replay_input,
        own_state_row_id=own.id if own is not None else None, is_new=is_new,
    )


async def _load_new_events(
    db: AsyncSession, user_id: int, refs: list[NewEventRef], head: AthleteState
) -> list[ReplayEvent]:
    introduced = await _introduced_ids(db)
    events: list[ReplayEvent] = []
    for ordinal, ref in enumerate(refs):
        if ref.kind == "workout":
            w = await db.get(WorkoutLog, ref.event_id)
            if w is None or w.user_id != user_id:
                raise ReplayUnsupported("ownership", f"workout {ref.event_id}")
            _require_record_only(w.state_disposition, w.state_disposition_reason, f"workout {w.id}")
            if ("workout", w.id) in introduced:
                raise ReplayUnsupported("already_introduced", f"workout {w.id}")
            events.append(_workout_event(w, row_key=NEW_EVENT_ROW_KEY, ordinal=ordinal, own=None, is_new=True))
        elif ref.kind == "benchmark":
            o = await db.get(BenchmarkObservation, ref.event_id)
            if o is None or o.user_id != user_id:
                raise ReplayUnsupported("ownership", f"observation {ref.event_id}")
            _require_record_only(o.state_disposition, o.state_disposition_reason, f"observation {o.id}")
            if ("benchmark", o.id) in introduced:
                raise ReplayUnsupported("already_introduced", f"observation {o.id}")
            events.append(_benchmark_event(o, row_key=NEW_EVENT_ROW_KEY, ordinal=ordinal, own=None, is_new=True))
        else:
            raise ReplayUnsupported("not_record_only", f"unknown event kind {ref.kind!r}")
    for e in events:
        if e.timestamp >= head.timestamp:
            raise ReplayUnsupported("not_late", f"{e.kind} {e.event_id} is not before the head")
    return events


def _require_record_only(disposition: str | None, reason: str | None, what: str) -> None:
    if disposition != "record_only":
        raise ReplayUnsupported("not_record_only", f"{what} is {disposition!r}")
    if reason != "event_before_current_state":
        raise ReplayUnsupported("unsupported_reason", f"{what}: {reason!r}")


async def _introduced_ids(db: AsyncSession) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for w_id, o_id in (
        await db.execute(
            select(StateCorrectionEvent.workout_log_id, StateCorrectionEvent.observation_id)
        )
    ).all():
        out.add(("workout", w_id) if w_id is not None else ("benchmark", o_id))
    return out


async def _members(
    db: AsyncSession,
    user_id: int,
    ck: AthleteState,
    rows: list[AthleteState],
    corrections: list[StateCorrection],
    correction_events: dict[int, list[StateCorrectionEvent]],
    correction_heads: dict[int, AthleteState],
) -> list[ReplayEvent]:
    """Every event after the checkpoint, exactly once: those that wrote their own row, those a
    correction introduced, and applied benchmarks that wrote no row."""
    repo = AthleteContextRepository(db)
    members: list[ReplayEvent] = []
    seen: set[tuple[str, int]] = set()

    def add(event: ReplayEvent) -> None:
        ident = (event.kind, event.event_id)
        if ident in seen:
            raise ReplayUnsupported("double_membership", f"{event.kind} {event.event_id}")
        seen.add(ident)
        members.append(event)

    w_rows = {r.source_workout_log_id: r for r in rows if r.source_workout_log_id is not None}
    o_rows = {r.source_observation_id: r for r in rows if r.source_observation_id is not None}
    workouts = {
        w.id: w for w in (
            await db.execute(select(WorkoutLog).where(WorkoutLog.id.in_(list(w_rows))))
        ).scalars()
    } if w_rows else {}
    observations = {
        o.id: o for o in (
            await db.execute(select(BenchmarkObservation).where(BenchmarkObservation.id.in_(list(o_rows))))
        ).scalars()
    } if o_rows else {}

    for wid, row in w_rows.items():
        w = workouts.get(wid)
        if w is None or w.user_id != user_id:
            raise ReplayUnsupported("ownership", f"workout {wid} of state row {row.id}")
        if w.state_disposition != "applied":
            raise ReplayUnsupported("disposition_conflict", f"workout {wid} wrote row {row.id} but is {w.state_disposition!r}")
        add(_workout_event(w, row_key=row.id, ordinal=0, own=row, is_new=False))
    for oid, row in o_rows.items():
        o = observations.get(oid)
        if o is None or o.user_id != user_id:
            raise ReplayUnsupported("ownership", f"observation {oid} of state row {row.id}")
        if o.state_disposition != "applied":
            raise ReplayUnsupported("disposition_conflict", f"observation {oid} wrote row {row.id} but is {o.state_disposition!r}")
        add(_benchmark_event(o, row_key=row.id, ordinal=0, own=row, is_new=False))

    # Introduced by a correction: placed by the correction head's row id, then batch order.
    for c in corrections:
        head_row = correction_heads[c.id]
        for ce in correction_events[c.id]:
            if (ce.workout_log_id is not None) == (ce.observation_id is not None):
                raise ReplayUnsupported("lineage", f"correction {c.id} event {ce.id}")
            if ce.workout_log_id is not None:
                w = await db.get(WorkoutLog, ce.workout_log_id)
                if w is None or w.user_id != user_id:
                    raise ReplayUnsupported("ownership", f"workout {ce.workout_log_id}")
                event = _workout_event(w, row_key=head_row.id, ordinal=ce.ordinal, own=None, is_new=False)
            else:
                assert ce.observation_id is not None
                o = await db.get(BenchmarkObservation, ce.observation_id)
                if o is None or o.user_id != user_id:
                    raise ReplayUnsupported("ownership", f"observation {ce.observation_id}")
                event = _benchmark_event(o, row_key=head_row.id, ordinal=ce.ordinal, own=None, is_new=False)
            if (event.timestamp, head_row.id) > _key(ck):
                add(event)

    # Applied events that wrote no row have no place in the order. A workout always writes one;
    # a benchmark writes none only as an initial prior or a decline-held reading, both of which
    # the stepper refuses.
    candidates_w = list(
        (await db.execute(
            select(WorkoutLog).where(
                WorkoutLog.user_id == user_id, WorkoutLog.state_disposition == "applied",
                WorkoutLog.session_timestamp >= ck.timestamp,
            )
        )).scalars()
    )
    own_w = {
        r.source_workout_log_id: r
        for r in await repo.list_states_by_workout_ids([w.id for w in candidates_w])
    }
    for w in candidates_w:
        if ("workout", w.id) in seen:
            continue
        own = own_w.get(w.id)
        if own is None:
            raise ReplayUnsupported("event_without_row", f"workout {w.id}")
        if _key(own) > _key(ck):
            raise ReplayUnsupported("lineage", f"workout {w.id}'s row {own.id} is outside the tail")
    candidates_o = list(
        (await db.execute(
            select(BenchmarkObservation).where(
                BenchmarkObservation.user_id == user_id,
                BenchmarkObservation.state_disposition == "applied",
                BenchmarkObservation.observed_at >= ck.timestamp,
            )
        )).scalars()
    )
    own_o = {
        r.source_observation_id: r
        for r in await repo.list_states_by_observation_ids([o.id for o in candidates_o])
    }
    for o in candidates_o:
        if ("benchmark", o.id) in seen:
            continue
        if o.id in own_o:
            continue  # its row sits before the checkpoint
        raise ReplayUnsupported("event_without_row", f"observation {o.id}")
    return members


def _prove(
    ck_state: UnifiedStateVector, members: list[ReplayEvent], rows: list[AthleteState],
    marks: list[CorrectionMark],
) -> int:
    """Replay the stored events from the checkpoint and require every stored, non-stale row to
    be reproduced exactly. Returns how many rows were checked."""
    ordered = order_events(members)
    steps = replay(ck_state, ordered)

    for step in steps:
        ev = step.event
        plain = ev.own_state_row_id is not None  # wrote its own row: not introduced by a correction
        if ev.kind == "benchmark" and plain:
            # What the transition produced now must match what the live writer recorded then:
            # a row exactly when the capture says one was written.
            captured = bool(ev.replay_input.get("state_row_written"))
            if not (step.wrote_row == captured == (ev.own_state_row_id is not None)):
                raise ReplayUnsupported("row_flag_mismatch", f"observation {ev.event_id}")

    by_event = {(s.event.kind, s.event.event_id): s for s in steps}
    checked = 0
    for row in rows:
        if is_stale(row.id, row.timestamp, marks):
            continue
        if row.event_kind == "correction":
            at = (row.timestamp, row.id, 0)
            state = ck_state
            for s in steps:
                if s.event.position < at:
                    state = s.state
            expected = state
        else:
            ident = ("workout", row.source_workout_log_id) if row.event_kind == "workout" else ("benchmark", row.source_observation_id)
            step = by_event.get(ident)  # type: ignore[arg-type]
            if step is None:
                raise ReplayUnsupported("lineage", f"row {row.id} has no replayed event")
            expected = step.state
        bad = diff_columns(state_columns(expected), row_columns(row))
        if bad:
            raise ReplayUnsupported("reconstruction_mismatch", f"row {row.id}: {', '.join(bad)}")
        checked += 1
    return checked


async def plan_tail_replay(
    db: AsyncSession,
    user_id: int,
    new_events: list[NewEventRef],
    *,
    max_gap: timedelta | None = None,
    max_events: int | None = None,
) -> TailReplayPlan:
    """Prove the stored history reproducible, then fold ``new_events`` into it. Read-only."""
    if not new_events:
        raise ReplayUnsupported("no_new_events")
    digest = await _registry_ok(db)
    head = await AthleteContextRepository(db).get_latest_state(user_id)
    if head is None:
        raise ReplayUnsupported("no_state")
    new = await _load_new_events(db, user_id, new_events, head)
    t_min = min(e.timestamp for e in new)

    corrections, correction_events, correction_heads = await _load_corrections(db, user_id)
    marks = [CorrectionMark(correction_heads[c.id].id, c.affected_from) for c in corrections]
    if is_stale(head.id, head.timestamp, marks):
        raise ReplayUnsupported("lineage", f"head row {head.id} is stale")

    ck = await _select_checkpoint(db, user_id, t_min, marks)
    rows = list(await AthleteContextRepository(db).list_states_after(user_id, ck.timestamp, ck.id))
    _check_rows(ck, rows, marks, digest)
    if (rows[-1] if rows else ck).id != head.id:
        raise ReplayUnsupported("lineage", "the tail does not end at the head")

    members = await _members(db, user_id, ck, rows, corrections, correction_events, correction_heads)
    try:
        ck_state = unified_from_athlete_row_strict(ck)
    except EngineStateDecodeError as exc:
        raise ReplayUnsupported("undecodable_state", f"checkpoint row {ck.id}: {exc}") from exc
    rows_proven = _prove(ck_state, members, rows, marks)

    gap = head.timestamp - t_min
    if max_gap is not None and gap > max_gap:
        raise ReplayUnsupported("window_exceeded", f"{gap} > {max_gap}")
    if max_events is not None and len(members) + len(new) > max_events:
        raise ReplayUnsupported("tail_too_long", f"{len(members) + len(new)} > {max_events}")

    ordered = order_events([*members, *new])
    steps = replay(ck_state, ordered)
    corrected = steps[-1].state
    if corrected.timestamp != head.timestamp:
        raise ReplayUnsupported("head_time_mismatch", f"{corrected.timestamp} != {head.timestamp}")
    return TailReplayPlan(
        user_id=user_id, algorithm_version=ALGORITHM_VERSION, transition_identity=digest,
        checkpoint_row_id=ck.id, head_before_row_id=head.id, affected_from=t_min, gap=gap,
        members=tuple(order_events(members)), new_events=tuple(new), steps=tuple(steps),
        corrected_head=corrected, rows_proven=rows_proven,
    )
