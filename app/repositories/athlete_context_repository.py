"""Data-access boundary for athlete-context records.

See ``CONTEXT.md``. The repository owns persistence mechanics only — it reads
and writes ORM rows and contains no domain logic (no dose calculation, state
updates, or benchmark mapping). Callers convert ORM rows to domain vectors via
``app.engine.state_bridge`` in the service/engine layer.

The interface grows one migrated query at a time; methods are added only as
call sites are routed through them, so every method here has real callers.

Exception (AUD-C24): ``list_wellness_history`` returns immutable
``WellnessHistoryPoint`` **projections**, not ``WellnessSample`` ORM rows, so the
shadow feature-builder never holds a live entity an earlier shadow's rollback
could expire. This is a flat scalar projection, not a ``state_bridge`` domain-vector
mapping, so it does not pull domain logic into the repository — the rule this
boundary exists to keep.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.athlete_state import AthleteState
from app.models.wellness import WellnessSample
from app.models.workout_log import WorkoutLog


@dataclass(frozen=True, slots=True)
class WellnessHistoryPoint:
    """Immutable projection of one wellness observation's feature-building inputs.

    Exactly the fields the personalization recovery frame consumes (``recorded_date`` plus the
    three z-scored signals) — not the full ``WellnessSample`` surface. ``None`` (missing) is
    preserved distinctly from a real ``0.0``.
    """

    recorded_date: date
    sleep_hours: float | None
    hrv_ms: float | None
    resting_hr: float | None


class AthleteContextRepository:
    """Async persistence boundary over a provided :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----- State -----
    async def get_latest_state(self, user_id: int) -> AthleteState | None:
        """Most recent ``AthleteState`` for ``user_id``, or ``None`` if the
        athlete has no recorded state yet."""
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            # id breaks timestamp ties deterministically: rows can share a timestamp
            # (a workout and its e1RM observations), and "latest" must not be arbitrary.
            .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_recent_states(self, user_id: int, limit: int) -> Sequence[AthleteState]:
        """The athlete's most recent ``AthleteState`` rows, newest first (≤ ``limit``).

        Ties on ``timestamp`` break by ``id`` DESC so the ordering is total and
        deterministic — the state-history scrub is row-INDEXED, so an unstable tie
        order would shuffle which snapshot a slider position maps to between reloads.
        """
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def latest_state_at_or_before(
        self, user_id: int, ts: datetime
    ) -> AthleteState | None:
        """The athlete's most recent ``AthleteState`` with ``timestamp <= ts``, or ``None``.

        Bounded to one row, so a point-in-time read never loads the athlete's whole
        history (contrast ``list_states_ascending``). ``ts`` is naive UTC, like the column.
        Ties on ``timestamp`` break by ``id`` DESC — the same total order as
        ``list_recent_states``.
        """
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id, AthleteState.timestamp <= ts)
            .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_states_at_or_before(
        self, user_id: int, ts: datetime, *, limit: int, offset: int = 0
    ) -> Sequence[AthleteState]:
        """Rows with ``timestamp <= ts``, newest first, a page at a time (tail replay's
        checkpoint search walks back past rows a correction made stale)."""
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id, AthleteState.timestamp <= ts)
            .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_states_after(
        self, user_id: int, ts: datetime, row_id: int
    ) -> Sequence[AthleteState]:
        """Rows strictly after position ``(ts, row_id)`` in the chain's total order, oldest
        first: later in time, or the same time with a higher id."""
        result = await self.session.execute(
            select(AthleteState)
            .where(
                AthleteState.user_id == user_id,
                or_(
                    AthleteState.timestamp > ts,
                    and_(AthleteState.timestamp == ts, AthleteState.id > row_id),
                ),
            )
            .order_by(AthleteState.timestamp, AthleteState.id)
        )
        return result.scalars().all()

    async def list_states_by_workout_ids(self, ids: Sequence[int]) -> Sequence[AthleteState]:
        """The rows written by the given workouts (``source_workout_log_id``)."""
        if not ids:
            return []
        result = await self.session.execute(
            select(AthleteState).where(AthleteState.source_workout_log_id.in_(ids))
        )
        return result.scalars().all()

    async def list_states_by_observation_ids(self, ids: Sequence[int]) -> Sequence[AthleteState]:
        """The rows written by the given benchmark observations (``source_observation_id``)."""
        if not ids:
            return []
        result = await self.session.execute(
            select(AthleteState).where(AthleteState.source_observation_id.in_(ids))
        )
        return result.scalars().all()

    async def list_states_by_correction_ids(self, ids: Sequence[int]) -> Sequence[AthleteState]:
        """The correction heads (``source_correction_id``) of the given receipts."""
        if not ids:
            return []
        result = await self.session.execute(
            select(AthleteState).where(AthleteState.source_correction_id.in_(ids))
        )
        return result.scalars().all()

    async def list_states_ascending(self, user_id: int) -> Sequence[AthleteState]:
        """The athlete's full ``AthleteState`` history, oldest first (for feature-building)."""
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp, AthleteState.id)
        )
        return result.scalars().all()

    # ----- Wellness -----
    async def list_wellness_history(
        self,
        user_id: int,
        *,
        through_date: date | None = None,
        limit: int | None = None,
    ) -> list[WellnessHistoryPoint]:
        """The athlete's wellness history as immutable projections, oldest first.

        Athlete-scoped; ordered by ``(date, id)`` so the sequence is deterministic when
        multiple sources share a day; empty history returns ``[]``. Returns projections, never
        ``WellnessSample`` entities, so session lifecycle cannot affect the returned values
        (AUD-C24).
        """
        conditions = [WellnessSample.user_id == user_id]
        if through_date is not None:
            conditions.append(WellnessSample.date <= through_date)
        stmt = (
            select(
                WellnessSample.date,
                WellnessSample.sleep_hours,
                WellnessSample.hrv_ms,
                WellnessSample.resting_hr,
            )
            .where(*conditions)
            .order_by(WellnessSample.date, WellnessSample.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = await self.session.execute(stmt)
        return [
            WellnessHistoryPoint(
                recorded_date=row.date,
                sleep_hours=row.sleep_hours,
                hrv_ms=row.hrv_ms,
                resting_hr=row.resting_hr,
            )
            for row in rows
        ]

    # ----- Workouts -----
    async def list_recent_workouts(self, user_id: int, limit: int) -> Sequence[WorkoutLog]:
        """The athlete's most recently logged ``WorkoutLog`` rows, newest first (≤ ``limit``)."""
        result = await self.session.execute(
            select(WorkoutLog)
            .where(WorkoutLog.user_id == user_id)
            .order_by(WorkoutLog.logged_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
