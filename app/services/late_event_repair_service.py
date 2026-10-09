"""Fold the late events that were recorded but never applied (P3c).

``fold_late_events`` runs when an event arrives, behind ``APPLY_LATE_EVENTS``. Events that arrived
earlier (before the flag, or refused for a reason that has since gone) are still record-only.
This finds them and folds them in, one at a time, through the same function: same proof, same
policy, same atomic apply. Nothing here is a second implementation of a correction.

What it can and cannot repair:

* **Can:** record-only events from ``a055`` on, which carry their replay input.
* **Cannot:** events recorded before ``a055``. They have no captured inputs, and the rows
  around them are untrusted history; they are counted (``not_capturable``) and left alone. The
  approximate repair that would have covered them was abandoned in favour of exact replay.

Order. Events are folded oldest first. A fold places the new event after anything already at its
timestamp, because it arrived later; that is only right if the events are folded in arrival
order. So an event is skipped (``ambiguous_tie``) when another event of the same athlete shares
its exact timestamp and the order cannot be recovered: one of the other kind (workouts and
observations have no common arrival clock), or one of the same kind that arrived later and is
already in the state. A same-kind event that arrived earlier is fine, and so is one that arrived
later but is still waiting: it folds after this one, in id order.

**Discovery is a hint; the decision is made under the athlete's chain lock.** The list of waiting
events is read without the lock and goes stale: a live writer can fold a later event at the same
timestamp, or fold the candidate itself, at any moment. So for every candidate, the chain lock is
taken first and held through the fold and its commit, and only then is the candidate re-read and
its eligibility (still waiting, captured, no tie that breaks the order) decided from fresh data.
A candidate that stopped being eligible is counted (``gone``, ``ambiguous_tie``) and skipped.

Locking. An apply run takes the lock per candidate and commits after each, so an athlete's writers
wait at most one fold. A dry run is different: its folds must stay visible to the next one, so it
is one transaction, and it holds the athlete's lock from its first candidate until its final
rollback. Its report equals what ``--apply`` would do only if the athlete's history does not
change in between.

Repeat runs. Only folded events leave the waiting set. Refused, not-capturable and ambiguous
events are reported again every run (a refusal code is rewritten each time ``--apply`` retries).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.benchmark_observation import BenchmarkObservation
from app.models.workout_log import WorkoutLog
from app.services.late_event_service import fold_late_events
from app.services.state_chain_lock import lock_athlete_chain
from app.services.tail_replay_service import NewEventRef

logger = logging.getLogger(__name__)

_REASON = "event_before_current_state"

_ELIGIBLE = "eligible"
_GONE = "gone"
_NOT_CAPTURABLE = "not_capturable"
_AMBIGUOUS = "ambiguous_tie"


@dataclass
class AthleteRepair:
    user_id: int
    considered: int = 0  # record-only events found when the run looked
    folded: int = 0
    refused: dict[str, int] = field(default_factory=lambda: {})
    not_capturable: int = 0  # record-only events written before capture existed
    ambiguous_tie: int = 0
    gone: int = 0  # no longer waiting when their turn came (a writer folded them first)


@dataclass
class RepairReport:
    athletes: list[AthleteRepair]
    applied: bool

    @property
    def folded(self) -> int:
        return sum(a.folded for a in self.athletes)


@dataclass(frozen=True)
class _Waiting:
    kind: str
    event_id: int
    timestamp: datetime


async def users_with_pending_events(db: AsyncSession) -> list[int]:
    ids: set[int] = set()
    for model in (WorkoutLog, BenchmarkObservation):
        rows = await db.execute(
            select(model.user_id)
            .where(model.state_disposition == "record_only", model.state_disposition_reason == _REASON)
            .distinct()
        )
        ids.update(uid for (uid,) in rows)
    return sorted(ids)


async def _discover(db: AsyncSession, user_id: int) -> list[_Waiting]:
    """The athlete's waiting events, oldest first. Unlocked, so only a list of candidates."""
    waiting: list[_Waiting] = []
    for wid, ts in (await db.execute(
        select(WorkoutLog.id, WorkoutLog.session_timestamp).where(
            WorkoutLog.user_id == user_id, WorkoutLog.state_disposition == "record_only",
            WorkoutLog.state_disposition_reason == _REASON,
        )
    )).all():
        waiting.append(_Waiting("workout", wid, ts))
    for oid, ts in (await db.execute(
        select(BenchmarkObservation.id, BenchmarkObservation.observed_at).where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkObservation.state_disposition == "record_only",
            BenchmarkObservation.state_disposition_reason == _REASON,
        )
    )).all():
        waiting.append(_Waiting("benchmark", oid, ts))
    return sorted(waiting, key=lambda w: (w.timestamp, w.kind, w.event_id))


async def _classify(db: AsyncSession, user_id: int, w: _Waiting) -> str:
    """Decide from fresh data. The caller holds the athlete's chain lock."""
    row: WorkoutLog | BenchmarkObservation | None
    if w.kind == "workout":
        row = (await db.execute(
            select(WorkoutLog).where(WorkoutLog.id == w.event_id).execution_options(populate_existing=True)
        )).scalar_one_or_none()
        timestamp = row.session_timestamp if row is not None else w.timestamp
    else:
        row = (await db.execute(
            select(BenchmarkObservation).where(BenchmarkObservation.id == w.event_id)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        timestamp = row.observed_at if row is not None else w.timestamp
    if (
        row is None or row.user_id != user_id or row.state_disposition != "record_only"
        or row.state_disposition_reason != _REASON
    ):
        return _GONE
    if row.replay_input is None:
        return _NOT_CAPTURABLE

    others: list[tuple[str, int, str | None]] = []
    for wid, disposition in (await db.execute(
        select(WorkoutLog.id, WorkoutLog.state_disposition).where(
            WorkoutLog.user_id == user_id, WorkoutLog.session_timestamp == timestamp)
    )).all():
        others.append(("workout", wid, disposition))
    for oid, disposition in (await db.execute(
        select(BenchmarkObservation.id, BenchmarkObservation.state_disposition).where(
            BenchmarkObservation.user_id == user_id, BenchmarkObservation.observed_at == timestamp)
    )).all():
        others.append(("benchmark", oid, disposition))

    for kind, event_id, disposition in others:
        if (kind, event_id) == (w.kind, w.event_id):
            continue
        if kind != w.kind:
            return _AMBIGUOUS  # workouts and observations have no common arrival clock
        if event_id < w.event_id:
            continue  # arrived first: the new event correctly goes after it
        # Arrived later: harmless while it is still waiting (it folds after this one, in id
        # order); wrong if it is already in the state.
        if disposition == "applied":
            return _AMBIGUOUS
    return _ELIGIBLE


async def repair_athlete(db: AsyncSession, user_id: int, *, apply: bool) -> AthleteRepair:
    result = AthleteRepair(user_id=user_id)
    candidates = await _discover(db, user_id)
    result.considered = len(candidates)
    for w in candidates:
        # The lock first, held through the fold and its commit; everything below reads fresh.
        await lock_athlete_chain(db, user_id)
        verdict = await _classify(db, user_id, w)
        if verdict == _ELIGIBLE:
            outcome = await fold_late_events(db, user_id, [NewEventRef(w.kind, w.event_id)])
            if outcome.applied:
                result.folded += 1
            else:
                code = outcome.refusal or "unknown"
                result.refused[code] = result.refused.get(code, 0) + 1
        elif verdict == _GONE:
            result.gone += 1
        elif verdict == _NOT_CAPTURABLE:
            result.not_capturable += 1
        else:
            result.ambiguous_tie += 1
        if apply:
            await db.commit()  # each fold is atomic; the chain lock ends here
    if not apply:
        await db.rollback()  # a dry run does everything, then keeps none of it
    return result


async def repair_all(
    session_factory: async_sessionmaker[AsyncSession], *, apply: bool, user_id: int | None = None
) -> RepairReport:
    """Repair every athlete with pending events (or just ``user_id``), one session each."""
    async with session_factory() as db:
        users = [user_id] if user_id is not None else await users_with_pending_events(db)
    athletes: list[AthleteRepair] = []
    for uid in users:
        async with session_factory() as db:
            athletes.append(await repair_athlete(db, uid, apply=apply))
    return RepairReport(athletes=athletes, applied=apply)
