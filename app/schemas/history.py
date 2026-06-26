"""
app/schemas/history.py

Read schemas for the history endpoints (app/api/v1/history.py). State history is
served as UnifiedStateVector[]; this adds the workout-log summary row.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)
