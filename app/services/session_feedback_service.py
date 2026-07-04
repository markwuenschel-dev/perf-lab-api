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
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mesocycle import PlannedSession
from app.models.telemetry import SessionFeedback
from app.models.workout_log import WorkoutLog
from app.schemas.session_feedback import SessionFeedbackIn


async def create_feedback(
    db: AsyncSession, user_id: int, payload: SessionFeedbackIn
) -> SessionFeedback:
    """Persist one athlete-reported ``SessionFeedback`` row for the caller.

    Raises ``HTTPException`` on ownership violations (404 — the resource does
    not exist *for this user*, mirroring the objectives/macrocycles pattern)
    and on a duplicate (409 — ``planned_session_id`` is unique).
    """
    # 1. The planned session must exist AND belong to the caller.
    planned_session = (
        await db.execute(
            select(PlannedSession).where(
                PlannedSession.id == payload.planned_session_id,
                PlannedSession.user_id == user_id,
            )
        )
    ).scalars().first()
    if planned_session is None:
        raise HTTPException(status_code=404, detail="Planned session not found")

    # 2. If a completed workout log is referenced, it must belong to the caller too.
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

    # 3. Feedback is one-per-session (planned_session_id is unique).
    existing = (
        await db.execute(
            select(SessionFeedback.id).where(
                SessionFeedback.planned_session_id == payload.planned_session_id
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Feedback already recorded for this session"
        )

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
    await db.commit()
    await db.refresh(feedback)
    return feedback
