from typing import Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.engine_vectors import AdaptationContribution, StressDoseSix


class ExerciseEntry(BaseModel):
    """
    A single exercise within a session log.

    `phi_*` and `energy_mix` are resolved from the Exercise DB row by the
    service layer before the dose engine runs. They are left blank in
    client-submitted logs; the engine falls back to modality defaults when absent.
    """

    exercise_id: Optional[int] = None
    exercise_name: Optional[str] = None

    sets: Optional[float] = Field(default=None, ge=1)
    reps: Optional[float] = Field(default=None, ge=0)
    load_kg: Optional[float] = Field(default=None, ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    distance_meters: Optional[float] = Field(default=None, ge=0)
    avg_rpe: Optional[float] = Field(default=None, ge=1, le=10)
    avg_rir: Optional[float] = Field(default=None, ge=0, le=10)
    tempo: Optional[str] = None
    rest_seconds: Optional[float] = Field(default=None, ge=0)

    # Resolved phi vectors (populated by service layer from Exercise DB row)
    phi_adapt: Optional[dict] = None
    phi_fatigue: Optional[dict] = None
    phi_tissue: Optional[dict] = None
    energy_mix: Optional[dict] = None

    # Resolved exercise metadata (populated by service layer)
    modality: Optional[str] = None
    movement_pattern: Optional[str] = None
    skill_demand: Optional[float] = None
    impact_level: Optional[float] = None
    recovery_cost: Optional[float] = None
    weak_point_tags: Optional[list[str]] = None
    sport_domains: Optional[list[str]] = None


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

    # Concrete exercise entries (optional; enables exercise-aware dose computation)
    exercises: list[ExerciseEntry] = Field(
        default_factory=list,
        description="Per-exercise breakdown. When present, dose reflects actual exercise phi vectors.",
    )


class StressDose(BaseModel):
    """
    Stress dose: six-dimensional engine vector plus legacy scalars for clients.

    `adaptation_contribution` is the session's positive adaptation signal per
    capacity axis. Used by state_update for explicit capacity gains.
    """

    dose_six: StressDoseSix = Field(default_factory=StressDoseSix)
    adaptation_contribution: AdaptationContribution = Field(
        default_factory=AdaptationContribution
    )

    d_met_systemic: float = 0.0
    d_nm_peripheral: float = 0.0
    d_nm_central: float = 0.0
    d_struct_damage: float = 0.0
    d_struct_signal: float = 0.0
