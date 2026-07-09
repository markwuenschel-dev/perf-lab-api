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


class WorkoutSetEntry(BaseModel):
    """
    A single logged set — the atomic unit of a workout (ADR-0045).

    Binds to a catalog ``Exercise`` (by ``exercise_id`` or ``exercise_name``); the
    exercise's ``load_type`` types which fields are meaningful. Movements not yet in
    the catalog log via ``free_text_name`` (no benchmark linkage). ``sets`` is a
    quick-entry multiplier: ``3×5 @ 100kg @ RPE8`` is one entry with ``sets=3`` that
    the service materializes into three editable ``workout_set_logs`` rows, so per-set
    RPE and the top set survive instead of being averaged away.
    """

    exercise_id: int | None = None
    exercise_name: str | None = None
    free_text_name: str | None = Field(
        default=None,
        description="Fallback name for a movement not in the catalog (no benchmark linkage).",
    )
    load_type: str | None = Field(
        default=None,
        description="Overrides the catalog load_type snapshot; usually left to the service.",
    )

    sets: int = Field(
        default=1,
        ge=1,
        le=50,
        description="Quick-entry multiplier: materialize this many identical set rows.",
    )
    # load_type-typed fields (which matter depends on load_type)
    load_kg: float | None = Field(default=None, ge=0)
    reps: int | None = Field(default=None, ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    distance_m: float | None = Field(default=None, ge=0)

    rpe: float | None = Field(default=None, ge=1, le=10)
    rir: float | None = Field(default=None, ge=0, le=10)
    is_top_set: bool | None = Field(
        default=None,
        description="Force this as the exercise's top set; else the service infers it "
        "(heaviest set) to drive e1RM extraction.",
    )

    # Bodyweight / execution modifiers
    band: str | None = None
    elevation: str | None = None
    tempo: str | None = None
    notes: str | None = None


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

    # Per-set breakdown (ADR-0045). The atomic logged unit; when present the sets
    # are persisted to workout_set_logs, the session modality is derived from them,
    # the dose reflects real per-set external load, and top sets emit e1RM
    # observations. Takes precedence over the legacy ``exercises`` breakdown.
    sets: list[WorkoutSetEntry] = Field(
        default_factory=lambda: [],
        description="Per-set log rows. When present, the session is a heterogeneous "
        "bag of sets and modality is derived (uniform → that modality, else Mixed).",
    )

    # Concrete exercise entries (optional; enables exercise-aware dose computation).
    # Legacy per-exercise breakdown; ``sets`` supersedes it when both are sent.
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
