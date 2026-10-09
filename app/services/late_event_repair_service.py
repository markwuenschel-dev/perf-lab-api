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

One athlete at a time, one fold per transaction (each takes the athlete's chain lock and commits,
so the lock is short). ``dry_run`` performs every fold and rolls them all back, so its report is
exactly what ``--apply`` would do.
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
from app.services.tail_replay_service import NewEventRef

logger = logging.getLogger(__name__)

_REASON = "event_before_current_state"


@dataclass
class AthleteRepair:
    user_id: int
    pending: int = 0  # record-only events with captured inputs that were considered
    folded: int = 0
    refused: dict[str, int] = field(default_factory=lambda: {})
    not_capturable: int = 0  # record-only events written before capture existed
    ambiguous_tie: int = 0


@dataclass
class RepairReport:
    athletes: list[AthleteRepair]
    applied: bool

    @property
    def folded(self) -> int:
        return sum(a.folded for a in self.athletes)


@dataclass(frozen=True)
class _Pending:
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


async def _pending(db: AsyncSession, user_id: int, result: AthleteRepair) -> list[_Pending]:
    """The record-only events to try, oldest first, minus those that cannot be repaired."""
    workouts = list((await db.execute(
        select(WorkoutLog).where(
            WorkoutLog.user_id == user_id, WorkoutLog.state_disposition == "record_only",
            WorkoutLog.state_disposition_reason == _REASON,
        )
    )).scalars())
    observations = list((await db.execute(
        select(BenchmarkObservation).where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkObservation.state_disposition == "record_only",
            BenchmarkObservation.state_disposition_reason == _REASON,
        )
    )).scalars())

    candidates: list[_Pending] = []
    for w in workouts:
        if w.replay_input is None:
            result.not_capturable += 1
        else:
            candidates.append(_Pending("workout", w.id, w.session_timestamp))
    for o in observations:
        if o.replay_input is None:
            result.not_capturable += 1
        else:
            candidates.append(_Pending("benchmark", o.id, o.observed_at))

    # Every event of this athlete, by timestamp, for the tie rule.
    by_time: dict[datetime, list[tuple[str, int, str | None]]] = {}
    for wid, ts, disposition in (await db.execute(
        select(WorkoutLog.id, WorkoutLog.session_timestamp, WorkoutLog.state_disposition).where(
            WorkoutLog.user_id == user_id)
    )).all():
        by_time.setdefault(ts, []).append(("workout", wid, disposition))
    for oid, ts, disposition in (await db.execute(
        select(
            BenchmarkObservation.id, BenchmarkObservation.observed_at,
            BenchmarkObservation.state_disposition,
        ).where(BenchmarkObservation.user_id == user_id)
    )).all():
        by_time.setdefault(ts, []).append(("benchmark", oid, disposition))

    def blocks(c: _Pending, kind: str, event_id: int, disposition: str | None) -> bool:
        """Does another event at this timestamp make folding ``c`` now risk the wrong order?"""
        if (kind, event_id) == (c.kind, c.event_id):
            return False
        if kind != c.kind:
            return True  # workouts and observations have no common arrival clock
        if event_id < c.event_id:
            return False  # arrived first: the new event correctly goes after it
        # Arrived later: harmless while it is still waiting (it folds after this one, in id
        # order); wrong if it is already in the state.
        return disposition == "applied"

    keep: list[_Pending] = []
    for c in candidates:
        if any(blocks(c, *other) for other in by_time[c.timestamp]):
            result.ambiguous_tie += 1
        else:
            keep.append(c)
    result.pending = len(candidates)
    return sorted(keep, key=lambda c: (c.timestamp, c.kind, c.event_id))


async def repair_athlete(db: AsyncSession, user_id: int, *, apply: bool) -> AthleteRepair:
    result = AthleteRepair(user_id=user_id)
    for event in await _pending(db, user_id, result):
        outcome = await fold_late_events(db, user_id, [NewEventRef(event.kind, event.event_id)])
        if outcome.applied:
            result.folded += 1
        else:
            code = outcome.refusal or "unknown"
            result.refused[code] = result.refused.get(code, 0) + 1
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
