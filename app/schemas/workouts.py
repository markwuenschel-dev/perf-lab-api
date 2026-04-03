from typing import Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.engine_vectors import StressDoseSix


class WorkoutLog(BaseModel):
    """
    Raw input log (Sensor Data).
    """

    timestamp: datetime
    modality: Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"]

    duration_minutes: float
    session_rpe: float = Field(..., ge=1, le=10)

    avg_rir: Optional[float] = None
    distance_meters: Optional[float] = 0.0
    total_volume_load: Optional[float] = 0.0

    # Optional execution hints for dose law
    dominant_movement_pattern: Optional[str] = Field(
        default=None,
        description="e.g. squat | hinge | run — defaults inferred from modality",
    )
    novelty: float = Field(
        1.0,
        ge=0.1,
        le=3.0,
        description=">1 = novel / high coordination demand for this athlete",
    )
    estimated_sets: Optional[float] = Field(
        default=None,
        ge=1.0,
        description="If set, refines volume term in dose law",
    )

    sleep_quality: float = Field(5.0, ge=1, le=10)
    life_stress_inverse: float = Field(
        5.0,
        ge=1,
        le=10,
        description="1 = Very high life stress, 10 = No life stress",
    )


class StressDose(BaseModel):
    """
    Stress dose: six-dimensional engine vector plus legacy scalars for clients.
    """

    dose_six: StressDoseSix = Field(default_factory=StressDoseSix)

    d_met_systemic: float = 0.0
    d_nm_peripheral: float = 0.0
    d_nm_central: float = 0.0
    d_struct_damage: float = 0.0
    d_struct_signal: float = 0.0
