from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.engine_vectors import AdaptationContribution, StressDoseSix


class ExerciseEntry(BaseModel):
    """
    A single exercise within a session log.

    `phi_*` and `energy_mix` are resolved from the Exercise DB row by the
    service layer before the dose engine runs. They are left blank in
    client-submitted logs; the engine falls back to modality defaults when absent.
    """

    exercise_id: int | None = None
    exercise_name: str | None = None

    sets: float | None = Field(default=None, ge=1)
    reps: float | None = Field(default=None, ge=0)
    load_kg: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    avg_rpe: float | None = Field(default=None, ge=1, le=10)
    avg_rir: float | None = Field(default=None, ge=0, le=10)
    tempo: str | None = None
    rest_seconds: float | None = Field(default=None, ge=0)

    # Resolved phi vectors (populated by service layer from Exercise DB row)
    phi_adapt: dict[str, Any] | None = None
    phi_fatigue: dict[str, Any] | None = None
    phi_tissue: dict[str, Any] | None = None
    energy_mix: dict[str, Any] | None = None

    # Resolved exercise metadata (populated by service layer)
    modality: str | None = None
    movement_pattern: str | None = None
    skill_demand: float | None = None
    impact_level: float | None = None
    recovery_cost: float | None = None
    weak_point_tags: list[str] | None = None
    sport_domains: list[str] | None = None


class WorkoutLog(BaseModel):
    """
    Raw input log (Sensor Data).
    """

    timestamp: datetime
    modality: Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"]

    duration_minutes: float
    session_rpe: float = Field(..., ge=1, le=10)

    avg_rir: float | None = None
    distance_meters: float | None = 0.0
    total_volume_load: float | None = 0.0

    # Optional execution hints for dose law
    dominant_movement_pattern: str | None = Field(
        default=None,
        description="e.g. squat | hinge | run — defaults inferred from modality",
    )
    novelty: float = Field(
        1.0,
        ge=0.1,
        le=3.0,
        description=">1 = novel / high coordination demand for this athlete",
    )
    estimated_sets: float | None = Field(
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
        default_factory=lambda: [],
        description="Per-exercise breakdown. When present, dose reflects actual exercise phi vectors.",
    )

    # Optional linkage to planning layer
    planned_session_id: int | None = Field(
        default=None,
        description="If provided, marks this log as fulfillment of the planned session.",
    )
    is_benchmark: bool = False
    benchmark_results: dict[str, Any] | None = Field(
        default=None,
        description="Optional benchmark key/value payload for benchmark sessions.",
    )


class StressDose(BaseModel):
    """
    Stress dose: six-dimensional engine vector plus legacy scalars for clients.

    `adaptation_contribution` is the session's positive adaptation signal per
    capacity axis. Used by state_update for explicit capacity gains.
    """

    dose_six: StressDoseSix = Field(default_factory=lambda: StressDoseSix())
    adaptation_contribution: AdaptationContribution = Field(
        default_factory=lambda: AdaptationContribution()
    )

    d_met_systemic: float = 0.0
    d_nm_peripheral: float = 0.0
    d_nm_central: float = 0.0
    d_struct_damage: float = 0.0
    d_struct_signal: float = 0.0
