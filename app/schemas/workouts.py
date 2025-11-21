from typing import Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field


class WorkoutLog(BaseModel):
    """
    Raw input log (Sensor Data).
    """
    timestamp: datetime
    modality: Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"]

    # External load
    duration_minutes: float
    session_rpe: float = Field(..., ge=1, le=10)

    # Optional fields based on modality
    avg_rir: Optional[float] = None
    distance_meters: Optional[float] = 0.0
    total_volume_load: Optional[float] = 0.0

    # Human Factors (1–10 scale; 5 = neutral)
    sleep_quality: float = Field(5.0, ge=1, le=10)
    life_stress_inverse: float = Field(
        5.0,
        ge=1,
        le=10,
        description="1 = Very high life stress, 10 = No life stress",
    )


class StressDose(BaseModel):
    """
    The Input Vector D(t) calculated from the log.
    """
    d_met_systemic: float = 0.0
    d_nm_peripheral: float = 0.0
    d_nm_central: float = 0.0
    d_struct_damage: float = 0.0
    d_struct_signal: float = 0.0
