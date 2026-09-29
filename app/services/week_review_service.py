"""Week review — what a block week held, what the state did, what is already set for next week.

Display-only and read-only: SELECTs only, and nothing it returns feeds prescription,
scoring or state. Rules (plan "Shared rules", B3):

* **Current block** — the most recently created ACTIVE block, the prescription precedent
  (``prescription_service._load_prescription_context``); several ACTIVE blocks may exist.
* **Today** — server-local ``date.today()``, as ``planning_service.get_today_session`` does.
* **Week membership** — a block week is ``block_id + week_number`` with dates
  ``block.start + (week-1)*7 … +6``. A session belongs by ``scheduled_date`` inside that
  window (ADR-0069: a move changes the date, not ``week_number``).
* **Prescribed RPE** — max exercise ``rpe_cap`` in the stored prescription; null when not
  prescribed (never guessed).
* **State** — decoded strictly. A decode failure is ``available=false, reason=state_invalid``
  (200, not a 409: this surface gates nothing). No state row at all is ``no_state``.

"Next week" lists only facts that are already determined — the block's own deload and
benchmark flags, the block ending, the prescriber's adherence bias, and plan-revision
triggers active on the latest state. Nothing here re-plans a future session: those are
placeholders whose content is resolved on the day, and accept/override (P12) is not
implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.engine.engine_state_codec import EngineStateDecodeError
from app.engine.state_loading import unified_from_athlete_row_strict
from app.logic.confidence_presentation import STATUS_INSUFFICIENT, confidence_status
from app.logic.constraint_engine import mean_fatigue
from app.logic.planned_session_log import prescribed_rpe
from app.logic.prescriber import MODIFICATION_FRICTION_WEIGHT, RECENT_SKIPS_BIAS_THRESHOLD
from app.logic.prescription_finalize import derive_plan_revision_triggers
from app.models.mesocycle import BlockStatus, MesocycleBlock, PlannedSession, SessionStatus
from app.models.telemetry import SessionFeedback
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.planning import (
    WeekReview,
    WeekReviewAxisMove,
    WeekReviewCounts,
    WeekReviewMoved,
    WeekReviewNextItem,
    WeekReviewSession,
    WeekReviewWindow,
)
from app.schemas.state import UnifiedStateVector
from app.services import state_service
from app.services.dashboard_service import compute_adherence_pct
from app.services.planning_service import block_adherence_signals


class WeekReviewNotFound(LookupError):
    """The requested block is not the caller's, or the week is outside it (router → 404)."""


# One definition of "prescribed RPE" shared with the C1b projection (ADR-0073): the max
# exercise ``rpe_cap`` in a stored prescription, None when there is none — never guessed.
prescribed_rpe_cap = prescribed_rpe


def feedback_reports_modification(fb: SessionFeedback | None) -> bool:
    """Row-level form of ``planning_service.block_adherence_signals``' ``modified`` predicate.

    An athlete's "modified" status counts even when no dimension was named. Only meaningful
    for a COMPLETED session — the caller applies that half of the rule, as the aggregate does.
    """
    if fb is None:
        return False
    return bool(
        fb.status == "modified"
        or fb.modified_volume
        or fb.modified_intensity
        or fb.modified_exercises
    )


def trigger_kind(axis: str) -> Literal["safety", "assess"]:
    """How a firing plan-revision trigger is labelled for the athlete.

    Fatigue and tissue-stress triggers protect the athlete (``safety``). A capacity trigger
    (``c_*`` — today only low aerobic capacity) says the evidence is thin, which is a
    measurement question, not a safety one (``assess``).
    """
    return "assess" if axis.startswith("c_") else "safety"


def week_window(block: MesocycleBlock, week_number: int) -> tuple[date, date]:
    start = block.start_date + timedelta(days=(week_number - 1) * 7)
    return start, start + timedelta(days=6)


def current_week_number(block: MesocycleBlock, today: date) -> int:
    """The block week containing ``today``, clamped to the block (before start ⇒ 1)."""
    if today < block.start_date:
        return 1
    return min(block.duration_weeks, (today - block.start_date).days // 7 + 1)


async def _current_block(db: AsyncSession, user_id: int) -> MesocycleBlock | None:
    result = await db.execute(
        select(MesocycleBlock)
        .where(MesocycleBlock.user_id == user_id, MesocycleBlock.status == BlockStatus.ACTIVE)
        .order_by(MesocycleBlock.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _owned_block(db: AsyncSession, user_id: int, block_id: int) -> MesocycleBlock | None:
    result = await db.execute(
        select(MesocycleBlock).where(
            MesocycleBlock.id == block_id, MesocycleBlock.user_id == user_id
        )
    )
    return result.scalars().first()


@dataclass(frozen=True)
class _SessionRow:
    session: PlannedSession
    felt_rpe: float | None
    feedback: SessionFeedback | None


async def _sessions_in_window(
    db: AsyncSession, user_id: int, block_id: int, start: date, end: date
) -> list[_SessionRow]:
    """The block's sessions dated in the window, outer-joined to their log and feedback.

    Both joins are at most one row per session: ``workout_log_id`` is a single FK and
    ``session_feedback.planned_session_id`` is unique — so there is no fan-out.
    """
    result = await db.execute(
        select(PlannedSession, WorkoutLog.session_rpe, SessionFeedback)
        .outerjoin(WorkoutLog, WorkoutLog.id == PlannedSession.workout_log_id)
        .outerjoin(SessionFeedback, SessionFeedback.planned_session_id == PlannedSession.id)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.block_id == block_id,
            PlannedSession.scheduled_date >= start,
            PlannedSession.scheduled_date <= end,
        )
        .order_by(PlannedSession.scheduled_date.asc(), PlannedSession.id.asc())
    )
    return [
        _SessionRow(session=ps, felt_rpe=rpe, feedback=fb)
        for ps, rpe, fb in result.tuples().all()
    ]


def _session_read(row: _SessionRow) -> WeekReviewSession:
    ps, fb = row.session, row.feedback
    return WeekReviewSession(
        planned_session_id=ps.id,
        scheduled_date=ps.scheduled_date,
        original_scheduled_date=ps.original_scheduled_date,
        week_number=ps.week_number,
        category=ps.category,
        modality=ps.modality,
        status=ps.status,
        is_deload=bool(ps.is_deload),
        is_benchmark=bool(ps.is_benchmark),
        workout_log_id=ps.workout_log_id,
        felt_rpe=row.felt_rpe,
        prescribed_rpe=prescribed_rpe_cap(ps.prescribed_content),
        feedback_status=fb.status if fb is not None else None,
        followed_as_prescribed=fb.followed_as_prescribed if fb is not None else None,
        modified=ps.status == SessionStatus.COMPLETED and feedback_reports_modification(fb),
        modified_volume=fb.modified_volume if fb is not None else None,
        modified_intensity=fb.modified_intensity if fb is not None else None,
        modified_exercises=fb.modified_exercises if fb is not None else None,
        modification_reason=fb.modification_reason if fb is not None else None,
    )


def _counts(sessions: list[WeekReviewSession], today: date) -> WeekReviewCounts:
    completed = sum(1 for s in sessions if s.status == SessionStatus.COMPLETED)
    due = sum(1 for s in sessions if s.scheduled_date <= today)
    # Adherence mirrors the dashboard: completed among the sessions already due.
    completed_due = sum(
        1 for s in sessions if s.scheduled_date <= today and s.status == SessionStatus.COMPLETED
    )
    return WeekReviewCounts(
        planned=len(sessions),
        completed=completed,
        skipped=sum(1 for s in sessions if s.status == SessionStatus.SKIPPED),
        modified=sum(1 for s in sessions if s.modified),
        pending=sum(1 for s in sessions if s.status == SessionStatus.PENDING),
        due=due,
        adherence_pct=compute_adherence_pct(completed_due, due),
    )


def _decode(row: Any) -> UnifiedStateVector | None:
    return unified_from_athlete_row_strict(row) if row is not None else None


def _moved(
    prev_start: UnifiedStateVector | None,
    start: UnifiedStateVector | None,
    end: UnifiedStateVector | None,
) -> WeekReviewMoved:
    axes: list[WeekReviewAxisMove] = []
    if end is not None:
        for axis in CapacityState.KEYS:
            status_end = confidence_status(getattr(end.capacity_confidence, axis))
            status_start = (
                confidence_status(getattr(start.capacity_confidence, axis))
                if start is not None
                else None
            )
            if status_end == STATUS_INSUFFICIENT:
                # An unrefined prior is not a measurement: no value, no delta.
                axes.append(
                    WeekReviewAxisMove(
                        axis=axis, measured=False, status_start=status_start, status_end=status_end
                    )
                )
                continue
            end_value = float(getattr(end.capacity_x, axis))
            start_value = (
                float(getattr(start.capacity_x, axis))
                if start is not None and status_start != STATUS_INSUFFICIENT
                else None
            )
            axes.append(
                WeekReviewAxisMove(
                    axis=axis,
                    measured=True,
                    status_start=status_start,
                    status_end=status_end,
                    start=round(start_value, 4) if start_value is not None else None,
                    end=round(end_value, 4),
                    delta=(
                        round(end_value - start_value, 4) if start_value is not None else None
                    ),
                )
            )

    def _mf(s: UnifiedStateVector | None) -> float | None:
        return round(mean_fatigue(s), 4) if s is not None else None

    return WeekReviewMoved(
        previous_week_start_snapshot_at=prev_start.timestamp if prev_start else None,
        start_snapshot_at=start.timestamp if start else None,
        end_snapshot_at=end.timestamp if end else None,
        mean_fatigue_previous_week_start=_mf(prev_start),
        mean_fatigue_start=_mf(start),
        mean_fatigue_end=_mf(end),
        capacity=axes,
    )


async def _next_week(
    db: AsyncSession,
    user_id: int,
    block: MesocycleBlock,
    week_number: int,
    latest: UnifiedStateVector,
) -> list[WeekReviewNextItem]:
    items: list[WeekReviewNextItem] = []

    # Safety first: plan-revision triggers currently active on the latest state. Approaching
    # triggers are excluded — they describe what *would* change the plan, not what will.
    for trig in derive_plan_revision_triggers(latest):
        if not trig.currently_active:
            continue
        items.append(
            WeekReviewNextItem(
                kind=trigger_kind(trig.axis),
                source=f"trigger:{trig.axis}",
                title=trig.label,
                reason=(
                    f"{trig.axis} is {trig.current_value:g} (threshold {trig.threshold:g}); "
                    f"the prescriber adjusts sessions for this until {trig.condition}."
                ),
            )
        )

    next_number = week_number + 1
    in_block = next_number <= block.duration_weeks
    if not in_block:
        items.append(
            WeekReviewNextItem(
                kind="plan",
                source="block:ends",
                title="Block ends",
                reason=(
                    f"Week {week_number} is the last of this {block.duration_weeks}-week block; "
                    "no sessions are planned after it."
                ),
            )
        )
    else:
        n_start, n_end = week_window(block, next_number)
        next_rows = await _sessions_in_window(db, user_id, block.id, n_start, n_end)
        next_sessions = [r.session for r in next_rows]
        if next_number == block.duration_weeks:
            items.append(
                WeekReviewNextItem(
                    kind="plan",
                    source="block:ends",
                    title="Final week of the block",
                    reason=(
                        f"Week {next_number} is the last of this {block.duration_weeks}-week "
                        "block."
                    ),
                )
            )
        if any(s.is_deload for s in next_sessions):
            items.append(
                WeekReviewNextItem(
                    kind="plan",
                    source="block:deload_week",
                    title="Deload week",
                    reason=(
                        f"Week {next_number} is a scheduled deload (every "
                        f"{block.deload_every_n_weeks} weeks); session volume is scaled by "
                        f"{block.deload_volume_factor:g}."
                    ),
                )
            )
        for s in next_sessions:
            if not s.is_benchmark:
                continue
            items.append(
                WeekReviewNextItem(
                    kind="assess",
                    source="block:benchmark_session",
                    title=f"Benchmark session on {s.scheduled_date.isoformat()}",
                    reason="The block schedules a periodic retest in this week.",
                )
            )

        # The prescriber's own adherence rule, over its own input (the block-wide
        # aggregate), so this line cannot disagree with what the next prescription does.
        # Only an ACTIVE block feeds the prescriber.
        if block.status == BlockStatus.ACTIVE:
            signals = await block_adherence_signals(db, user_id, block.id)
            skips = signals["recent_skips"]
            mods = signals["recent_modifications"]
            friction = skips + MODIFICATION_FRICTION_WEIGHT * mods
            if friction >= RECENT_SKIPS_BIAS_THRESHOLD:
                items.append(
                    WeekReviewNextItem(
                        kind="plan",
                        source="adherence:lighter_bias",
                        title="Lighter, more varied sessions favoured",
                        reason=(
                            f"{skips} skipped and {mods} modified session(s) in this block "
                            f"(friction {friction:g} ≥ {RECENT_SKIPS_BIAS_THRESHOLD}); the "
                            "prescriber biases toward variety/recovery work to rebuild adherence."
                        ),
                    )
                )
    return items


async def build_week_review(
    db: AsyncSession,
    user_id: int,
    *,
    block_id: int | None = None,
    week_number: int | None = None,
    today: date | None = None,
) -> WeekReview:
    """The week review for one block week (default: the current block's current week).

    Raises:
        WeekReviewNotFound: ``block_id`` is not the caller's, or ``week_number`` is outside it.
    """
    today = today or date.today()

    if block_id is not None:
        block = await _owned_block(db, user_id, block_id)
        if block is None:
            raise WeekReviewNotFound("Block not found")
    else:
        block = await _current_block(db, user_id)
        if block is None:
            return WeekReview(available=False, reason="no_active_block")

    if week_number is None:
        week_number = current_week_number(block, today)
    elif not 1 <= week_number <= block.duration_weeks:
        raise WeekReviewNotFound("Week not in block")

    start, end = week_window(block, week_number)
    window = WeekReviewWindow(
        block_id=block.id,
        week_number=week_number,
        duration_weeks=block.duration_weeks,
        start=start,
        end=end,
        is_current_week=start <= today <= end,
    )

    repo = AthleteContextRepository(db)
    try:
        latest = await state_service.load_current_state_strict(db, user_id)
        if latest is None:
            return WeekReview(available=False, reason="no_state", window=window)
        # Naive boundaries, like the column. The week-start snapshot is also the previous
        # week's end, so the third read brackets the previous week at its start instead.
        prev_start_ts = datetime.combine(start - timedelta(days=7), time.min)
        start_ts = datetime.combine(start, time.min)
        end_ts = datetime.combine(end, time.max)
        prev_start_state = _decode(await repo.latest_state_at_or_before(user_id, prev_start_ts))
        start_state = _decode(await repo.latest_state_at_or_before(user_id, start_ts))
        end_state = _decode(await repo.latest_state_at_or_before(user_id, end_ts))
    except EngineStateDecodeError:
        return WeekReview(available=False, reason="state_invalid", window=window)

    rows = await _sessions_in_window(db, user_id, block.id, start, end)
    sessions = [_session_read(r) for r in rows]
    next_items = await _next_week(db, user_id, block, week_number, latest)

    return WeekReview(
        available=True,
        window=window,
        sessions=sessions,
        counts=_counts(sessions, today),
        moved=_moved(prev_start_state, start_state, end_state),
        next_week=next_items,
        next_week_status="changes_listed" if next_items else "nothing_scheduled_to_change",
    )
