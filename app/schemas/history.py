"""
app/schemas/history.py

Read schemas for the history endpoints (app/api/v1/history.py). State history is
served as UnifiedStateVector[]; this adds the workout-log summary row.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkoutLogSummary(BaseModel):
    """A logged workout, trimmed to what history views render (recent sessions,
    weekly training load)."""

    id: int
    logged_at: datetime
    session_timestamp: datetime
    modality: str
    duration_minutes: float
    session_rpe: float
    distance_meters: float
    total_volume_load: float
    is_benchmark: bool
    # P3a. NULL: logged before dispositions were recorded.
    state_disposition: Literal["applied", "record_only"] | None = Field(
        default=None,
        description="Whether this workout updated the training state; null for older logs.",
    )
    state_disposition_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)
