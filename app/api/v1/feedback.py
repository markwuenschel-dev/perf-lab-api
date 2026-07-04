"""Session feedback — first-party adherence/satisfaction labels (write-path).

``POST /v1/feedback``  record an athlete-reported outcome for a planned session.

Data-capture only: this endpoint changes no prescription or decision. It simply
accumulates the labels the adaptive engine's research questions depend on.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.session_feedback import SessionFeedbackIn, SessionFeedbackOut
from app.services import session_feedback_service

router = APIRouter(prefix="/feedback", tags=["Feedback"])


@router.post("", response_model=SessionFeedbackOut, status_code=201)
async def create_feedback(
    payload: SessionFeedbackIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SessionFeedbackOut:
    """Persist one athlete-reported ``SessionFeedback`` row, user-scoped."""
    feedback = await session_feedback_service.create_feedback(db, current_user.id, payload)
    return SessionFeedbackOut.model_validate(feedback)
