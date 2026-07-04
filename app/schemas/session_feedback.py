"""Session feedback schemas — first-party adherence/satisfaction labels.

Data-capture only. ``SessionFeedbackIn`` carries the fields an athlete
actually reports about a planned session outcome; nothing here is inferred
from seeded exercise logs (see ``app.models.telemetry.SessionFeedback``).

``followed_as_prescribed`` MUST come from the athlete — never derive it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The four outcome states the model distinguishes (per the model docstring).
FeedbackStatus = Literal["completed", "skipped", "modified", "unknown"]


class SessionFeedbackIn(BaseModel):
    """Athlete-reported outcome for one planned session.

    ``planned_session_id`` must belong to the caller; ``completed_workout_log_id``
    (when given) must too — both are validated server-side to prevent IDOR.
    """

    planned_session_id: int
    completed_workout_log_id: int | None = None
    status: FeedbackStatus
    # Nullable-by-design: only set when the athlete actually reports it.
    followed_as_prescribed: bool | None = None
    modified_volume: bool = False
    modified_intensity: bool = False
    modified_exercises: bool = False
    modification_reason: str | None = None
    skip_reason: str | None = None
    satisfaction_score: int | None = Field(default=None, ge=1, le=5)
    perceived_fit_score: int | None = Field(default=None, ge=1, le=5)
    pain_flag: bool = False
    soreness_flag: bool = False
    notes: str | None = None


class SessionFeedbackOut(BaseModel):
    id: int
    planned_session_id: int
    completed_workout_log_id: int | None
    status: str
    followed_as_prescribed: bool | None
    modified_volume: bool
    modified_intensity: bool
    modified_exercises: bool
    modification_reason: str | None
    skip_reason: str | None
    satisfaction_score: int | None
    perceived_fit_score: int | None
    pain_flag: bool
    soreness_flag: bool
    notes: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
