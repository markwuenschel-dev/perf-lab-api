"""Session feedback service — persists first-party adherence/satisfaction labels.

The single write-path for ``SessionFeedback``. Every field stored is athlete-
reported (carried in on ``SessionFeedbackIn``); nothing is inferred from logs.

User scoping: ``SessionFeedback`` has no ``user_id`` of its own — ownership is
established transitively through the referenced ``PlannedSession`` (and, when
present, the ``WorkoutLog``). Both FKs are verified to belong to the caller so
an athlete can never file feedback against another athlete's session (IDOR).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.telemetry import SessionFeedback
from app.models.workout_log import WorkoutLog
from app.schemas.session_feedback import SessionFeedbackIn
from app.services.planned_session_protocol import lock_planned_session

# Feedback describes an outcome, so the session must already have one (ADR-0070).
# RESCHEDULED is deliberately absent: a moved session has not happened yet, and
# PENDING obviously has not either.
_TERMINAL_STATUSES = frozenset({SessionStatus.COMPLETED, SessionStatus.SKIPPED})

_DUPLICATE_DETAIL = "Feedback already recorded for this session"

# What the athlete may say about a session, given what the session says happened.
# `PlannedSession.status` owns occurrence (ADR-0070), so a report that contradicts
# it is refused rather than stored: a completed session cannot be reported skipped,
# and a skipped one cannot be reported completed or modified. "unknown" is allowed
# against either — declining to characterise an outcome contradicts nothing.
_COHERENT_OUTCOMES: dict[SessionStatus, frozenset[str]] = {
    SessionStatus.COMPLETED: frozenset({"completed", "modified", "unknown"}),
    SessionStatus.SKIPPED: frozenset({"skipped", "unknown"}),
}


async def create_feedback(
    db: AsyncSession, user_id: int, payload: SessionFeedbackIn
) -> SessionFeedback:
    """Persist one athlete-reported ``SessionFeedback`` row for the caller.

    Raises ``HTTPException`` on ownership violations (404 — the resource does
    not exist *for this user*, mirroring the objectives/macrocycles pattern)
    and on a duplicate (409 — ``planned_session_id`` is unique).
    """
    # 1. The planned session must exist AND belong to the caller. Locked (F3), so its
    # status cannot change between the checks below and the feedback insert.
    planned_session = await lock_planned_session(db, payload.planned_session_id, user_id)
    if planned_session is None:
        raise HTTPException(status_code=404, detail="Planned session not found")

    # 1b. The session must already be in a terminal state (ADR-0070). Feedback
    # describes an outcome; it never creates one, and it never writes `status` —
    # `PlannedSession.status` stays the single canonical source of occurrence, so
    # the adherence aggregate cannot be handed two disagreeing accounts of one
    # session.
    if planned_session.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Planned session is not complete or skipped yet; "
                "record the outcome on the session before giving feedback"
            ),
        )

    # 1c. The report must not contradict what the session says happened.
    allowed = _COHERENT_OUTCOMES.get(planned_session.status, frozenset())
    if payload.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session is {planned_session.status.value}; "
                f"feedback status must be one of {sorted(allowed)}"
            ),
        )

    # 2. If a completed workout log is referenced, it must belong to the caller AND
    # be the log this session was actually fulfilled by. Ownership alone is not
    # enough — it would let an athlete attach any of their own workouts to any of
    # their own sessions, making the link mean nothing to anything that later
    # trusts it.
    if payload.completed_workout_log_id is not None:
        workout_log = (
            await db.execute(
                select(WorkoutLog).where(
                    WorkoutLog.id == payload.completed_workout_log_id,
                    WorkoutLog.user_id == user_id,
                )
            )
        ).scalars().first()
        if workout_log is None:
            raise HTTPException(status_code=404, detail="Workout log not found")
        if planned_session.workout_log_id != workout_log.id:
            raise HTTPException(
                status_code=409,
                detail="That workout log did not fulfill this planned session",
            )

    # 3. Feedback is one-per-session (planned_session_id is unique).
    existing = (
        await db.execute(
            select(SessionFeedback.id).where(
                SessionFeedback.planned_session_id == payload.planned_session_id
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=_DUPLICATE_DETAIL)

    feedback = SessionFeedback(
        planned_session_id=payload.planned_session_id,
        completed_workout_log_id=payload.completed_workout_log_id,
        status=payload.status,
        followed_as_prescribed=payload.followed_as_prescribed,
        modified_volume=payload.modified_volume,
        modified_intensity=payload.modified_intensity,
        modified_exercises=payload.modified_exercises,
        modification_reason=payload.modification_reason,
        skip_reason=payload.skip_reason,
        satisfaction_score=payload.satisfaction_score,
        perceived_fit_score=payload.perceived_fit_score,
        pain_flag=payload.pain_flag,
        soreness_flag=payload.soreness_flag,
        notes=payload.notes,
    )
    db.add(feedback)
    # The check above narrows the window; it does not close it. Two concurrent
    # submissions can both find no existing row, and the loser then violates the
    # unique constraint on `planned_session_id`. An in-process guard cannot help —
    # API workers are separate processes — so the database arbitrates and both
    # callers are told the same thing rather than one receiving a 500.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=_DUPLICATE_DETAIL) from None
    await db.refresh(feedback)
    return feedback


async def list_feedback(
    db: AsyncSession, user_id: int, *, limit: int = 30
) -> list[SessionFeedback]:
    """The caller's most recent feedback rows, newest first.

    Scoped by joining ``PlannedSession`` rather than filtering a column:
    ``SessionFeedback`` has no ``user_id`` of its own, and putting the ownership
    predicate inside the query leaves no check-then-fetch gap.
    """
    result = await db.execute(
        select(SessionFeedback)
        .join(PlannedSession, SessionFeedback.planned_session_id == PlannedSession.id)
        .where(PlannedSession.user_id == user_id)
        .order_by(SessionFeedback.created_at.desc(), SessionFeedback.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
