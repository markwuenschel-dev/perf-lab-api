"""C1b planned-week projection (ADR-0073): the athlete's PENDING planned sessions, run forward
through the real engine, day by day.

Display-only. It writes nothing and is never an input to prescription or scoring (ADR-0064:
no invented future-readiness forecast). What it reports is modeled load and modeled FATIGUE,
not readiness — readiness is one backend-owned number that is not projected (PDR-0005).

Shape of the computation
------------------------
* **Start state** — ``state_service.load_current_state_strict``. No row -> ``no_state``; a row
  that cannot be decoded strictly -> ``state_invalid``. Both are ``available: false`` rather
  than an error: nothing is gated on this surface, and there is no ``Capability`` for it.
* **Catch-up** — the state is "as of" its timestamp; the time between then and the start of
  today is elapsed as rest first (``engine.simulate.rest_for``), so a state from last week is
  not projected as though it were this morning's.
* **Per day** — each pending session is turned into a synthetic log
  (``app.logic.planned_session_log``), dosed through the pinned production
  ``app.logic.dose_engine.calculate_stress_dose`` and applied with ``update_athlete_state``;
  then a zero-dose step elapses the rest of the day, and the end-of-day state is sampled.
  ``engine.simulate.run_schedule`` is deliberately not used: it returns one state per session
  and never elapses rest days.
* **Clock** — step times are NAIVE UTC at a fixed session hour: stored state timestamps are
  naive (``models/athlete_state.py``), and mixing aware datetimes would raise on subtraction.
  "Today" is the server-local ``date.today()``, as ``planning_service.get_today_session`` does.

Completed sessions are not re-projected: their effect is already in the state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.engine_state_codec import EngineStateDecodeError
from app.engine.simulate import make_log, rest_dose, rest_for
from app.logic.constraint_engine import mean_fatigue
from app.logic.dose_engine import calculate_stress_dose
from app.logic.planned_session_log import planned_session_to_log, prescribes_rest, session_basis
from app.logic.state_update_v0 import update_athlete_state
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.schemas.planning import (
    PlannedWeekDay,
    PlannedWeekFatigue,
    PlannedWeekProjection,
    PlannedWeekSession,
    PlannedWeekWindow,
)
from app.schemas.state import UnifiedStateVector
from app.services import planning_service, state_service
from app.services.dashboard_service import daily_load

#: Longest projectable window, inclusive of today.
MAX_WINDOW_DAYS = 28
#: Window when there is no current block week to end on (today + 6 = one week).
DEFAULT_WINDOW_DAYS = 7
#: Hour (naive UTC) a projected session is placed at; same-day sessions follow an hour apart.
SESSION_HOUR = 12
# Modality of the zero-dose rest log; its dose is zero, so the modality carries no load.
_REST_MODALITY = "Strength"
# What a session whose stored prescription is complete rest shows as (W1-c). A label, not an
# engine modality: the row carries no work and is never run through the dose law.
PRESCRIBED_REST_MODALITY = "Rest"


class ProjectionWindowError(ValueError):
    """``through`` is before today — there is nothing forward to project."""


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


def current_block_week(block: MesocycleBlock | None, today: date) -> tuple[int, date, date] | None:
    """(week_number, week_start, week_end) of the block week containing ``today``, else None.

    A block week is ``block.start + (week-1)*7 ... +6`` (ADR-0060). None when there is no
    block, today is before it starts, or past its last week.
    """
    if block is None or today < block.start_date:
        return None
    week_number = (today - block.start_date).days // 7 + 1
    if week_number > block.duration_weeks:
        return None
    week_start = block.start_date + timedelta(days=(week_number - 1) * 7)
    return week_number, week_start, week_start + timedelta(days=6)


def resolve_window(
    block: MesocycleBlock | None, today: date, through: date | None
) -> PlannedWeekWindow:
    """Today -> ``through`` (default: end of the current block week, else today+6), capped
    at ``MAX_WINDOW_DAYS`` inclusive. Raises ``ProjectionWindowError`` if ``through < today``.
    """
    week = current_block_week(block, today)
    if through is None:
        end = week[2] if week is not None else today + timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    else:
        if through < today:
            raise ProjectionWindowError("through must be today or later")
        end = through
    end = min(end, today + timedelta(days=MAX_WINDOW_DAYS - 1))
    return PlannedWeekWindow(
        start=today,
        end=end,
        block_id=block.id if (block is not None and week is not None) else None,
        week_number=week[0] if week is not None else None,
    )


# ---------------------------------------------------------------------------
# Pure engine rollout
# ---------------------------------------------------------------------------


def _naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts


def _fatigue(state: UnifiedStateVector) -> PlannedWeekFatigue:
    f = state.fatigue_f
    return PlannedWeekFatigue(
        cns=round(f.cns, 2),
        muscular=round(f.muscular, 2),
        metabolic=round(f.metabolic, 2),
        structural=round(f.structural, 2),
        tendon=round(f.tendon, 2),
        grip=round(f.grip, 2),
    )


def project_planned_days(
    state: UnifiedStateVector,
    start: date,
    end: date,
    sessions_by_day: Mapping[date, Sequence[tuple[PlannedSession, MesocycleBlock]]],
) -> tuple[list[PlannedWeekDay], float | None]:
    """Run ``start..end`` day by day through the engine. Pure and deterministic.

    Returns the per-day rows and the peak of their end-of-day mean fatigue (the same series
    the rows report, so the headline number agrees with the chart).
    """
    cur = state.model_copy(update={"timestamp": _naive_utc(state.timestamp)})
    day_start = datetime.combine(start, time(0, 0))
    gap_days = (day_start - cur.timestamp).total_seconds() / 86400.0
    if gap_days > 0:
        cur = rest_for(cur, days=gap_days)[-1]

    days: list[PlannedWeekDay] = []
    d = start
    while d <= end:
        rows: list[PlannedWeekSession] = []
        # Same-day TRAINING sessions are placed an hour apart. Count only those: a rest row is
        # zero work and takes no slot, so inserting one cannot move another workout's time
        # (and with it the decay before it, i.e. the forecast fatigue).
        trained = 0
        for session, block in sessions_by_day.get(d, ()):
            if prescribes_rest(session.prescribed_content):
                # The stored prescription is complete rest: zero work. Projecting it through
                # the block's target workout would show training fatigue and adaptation for a
                # day the athlete was told to rest. The day-end rest step below covers it.
                rows.append(
                    PlannedWeekSession(
                        planned_session_id=session.id,
                        modality=PRESCRIBED_REST_MODALITY,
                        basis=session_basis(session),
                        load=0.0,
                    )
                )
                continue
            when = datetime.combine(d, time(SESSION_HOUR, 0)) + timedelta(hours=trained)
            trained += 1
            log = planned_session_to_log(session, block, when)
            dt = when - cur.timestamp
            if dt.total_seconds() < 0:
                dt = timedelta(0)
            cur = update_athlete_state(cur, calculate_stress_dose(log), dt, log)
            rows.append(
                PlannedWeekSession(
                    planned_session_id=session.id,
                    modality=log.modality,
                    basis=session_basis(session),
                    load=round(daily_load(log.session_rpe, log.duration_minutes), 1),
                )
            )

        day_end = datetime.combine(d + timedelta(days=1), time(0, 0))
        rest_dt = day_end - cur.timestamp
        if rest_dt.total_seconds() > 0:
            rest_log = make_log(day_end, _REST_MODALITY, session_rpe=1.0)
            cur = update_athlete_state(cur, rest_dose(), rest_dt, rest_log)

        days.append(
            PlannedWeekDay(
                date=d,
                sessions=rows,
                load=round(sum(r.load for r in rows), 1),
                mean_fatigue=round(mean_fatigue(cur), 2),
                fatigue=_fatigue(cur),
            )
        )
        d += timedelta(days=1)

    peak = max((day.mean_fatigue for day in days), default=None)
    return days, peak


# ---------------------------------------------------------------------------
# DB entry point
# ---------------------------------------------------------------------------


async def _current_block(db: AsyncSession, user_id: int) -> MesocycleBlock | None:
    """Most recently created ACTIVE block — the prescription precedent
    (``prescription_service.prescribe_for_athlete``). Several ACTIVE blocks are allowed."""
    result = await db.execute(
        select(MesocycleBlock)
        .where(MesocycleBlock.user_id == user_id, MesocycleBlock.status == BlockStatus.ACTIVE)
        .order_by(MesocycleBlock.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def planned_week_projection(
    db: AsyncSession,
    user_id: int,
    through: date | None = None,
    *,
    today: date | None = None,
) -> PlannedWeekProjection:
    """The C1b projection for ``user_id``. Read-only.

    Raises ``ProjectionWindowError`` when ``through`` is before today (router -> 422).
    """
    today = today or date.today()
    window = resolve_window(await _current_block(db, user_id), today, through)

    try:
        state = await state_service.load_current_state_strict(db, user_id)
    except EngineStateDecodeError:
        # Display-only: a damaged state makes the projection unavailable, never a 409/500.
        return PlannedWeekProjection(available=False, reason="state_invalid", window=window)
    if state is None:
        return PlannedWeekProjection(available=False, reason="no_state", window=window)

    # Membership is by scheduled_date (ADR-0069: a move changes the date, not week_number),
    # so a moved session is projected where it now sits. Only PENDING sessions are future
    # work; completed ones are already in the state.
    sessions = [
        s
        for s in await planning_service.list_sessions(db, user_id, window.start, window.end)
        if s.status == SessionStatus.PENDING
    ]
    blocks: dict[int, MesocycleBlock] = {}
    block_ids = {s.block_id for s in sessions}
    if block_ids:
        result = await db.execute(select(MesocycleBlock).where(MesocycleBlock.id.in_(block_ids)))
        blocks = {b.id: b for b in result.scalars().all()}

    by_day: defaultdict[date, list[tuple[PlannedSession, MesocycleBlock]]] = defaultdict(list)
    for s in sessions:
        by_day[s.scheduled_date].append((s, blocks[s.block_id]))

    days, peak = project_planned_days(state, window.start, window.end, by_day)
    return PlannedWeekProjection(
        available=True, window=window, days=days, peak_mean_fatigue=peak
    )
