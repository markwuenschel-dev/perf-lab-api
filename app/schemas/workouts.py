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

    # Physical quantities: a session cannot last or cover a negative amount. Unbounded
    # before, which let nonsense reach the dose law and fail there instead of here — a
    # negative duration made ``log1p(V)`` raise a domain error mid-computation (a 500 on a
    # request that should be a 422).
    duration_minutes: float = Field(..., ge=0.0)
    session_rpe: float = Field(..., ge=1, le=10)

    avg_rir: float | None = Field(default=None, ge=0.0, le=10.0)
    distance_meters: float | None = Field(default=0.0, ge=0.0)
    total_volume_load: float | None = Field(default=0.0, ge=0.0)

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

    # Human factors (ADR-0049: "missing wellness signals are gaps, not imputed").
    # ``None`` means *no check-in exists for this session* and is carried as unknown all
    # the way to the engine, the ORM row (SQL NULL) and the calibration frame. It is
    # NEVER filled with the scale midpoint and NEVER carried forward from a prior
    # session — a silent 5.0 is indistinguishable from a real "5/10" report and would
    # manufacture false normality on exactly the days missingness is most informative.
    # When a value IS supplied it is a genuine athlete report bounded to [1, 10].
    sleep_quality: float | None = Field(
        default=None,
        ge=1,
        le=10,
        description="1 = Worst sleep, 10 = Best sleep. null = not reported: no dose "
        "penalty is applied and the dose is labelled neutral_missing with zero "
        "human-factor confidence (ADR-0049). Never imputed to a midpoint.",
    )
    life_stress_inverse: float | None = Field(
        default=None,
        ge=1,
        le=10,
        description="1 = Very high life stress, 10 = No life stress. null = not "
        "reported: no dose penalty is applied and the dose is labelled "
        "neutral_missing with zero human-factor confidence (ADR-0049).",
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


class IntensityContribution(BaseModel):
    """One exercise's contribution to the session external intensity (ADR-0039).

    Carries the intensity value **and its provenance** so the dose is auditable: the
    e1RM denominator that produced a ``relative_load`` reading (value + semantics +
    the observation it came from), which ladder rung was taken, and the aggregation
    weight (``w = reps · load``).
    """

    exercise_id: int | None = None
    exercise_name: str | None = None
    external_intensity: float
    source: str = Field(
        description="Ladder rung: relative_load | rpe_rir_chart | epley_failure | "
        "neutral_missing",
    )
    confidence: float = 0.0
    weight: float = Field(0.0, description="Aggregation weight w = reps · load.")
    # e1RM denominator provenance (only for a relative_load reading).
    e1rm_denominator_kg: float | None = None
    e1rm_source: str | None = None
    e1rm_value_semantics: str | None = None
    e1rm_observation_id: int | None = None


class ExternalIntensity(BaseModel):
    """The session-scalar external intensity ``I`` that entered the dose base (ADR-0039).

    Model A: a weighted session-level intensity replaces the old hardcoded ``1.0``.
    ``value`` is what the dose law raised to ``dose_alpha``; ``fallback_path`` names
    the rung that dominated; ``known_limitation`` records the ADR-0054 routing caveat
    (a session scalar shapes every exercise via the aggregate-φ path, so a hard
    accessory partially inherits the session's intensity).
    """

    # Load relative to capacity. Non-negative by construction, and the dose law raises it to
    # a FRACTIONAL exponent (``dose_alpha``) — a negative base there yields a complex number,
    # not a dose, which surfaced as a pydantic error while building StressDoseSix rather than
    # as a rejected input. 0.0 is permitted (no external load); negative is not a quantity.
    value: float = Field(default=1.0, ge=0.0)
    source: str = "neutral_missing"
    model_version: str = ""
    confidence: float = 0.0
    fallback_path: str = "session_no_external_load"
    known_limitation: str | None = None
    contributions: list[IntensityContribution] = Field(default_factory=lambda: [])


class HumanFactorInput(BaseModel):
    """One wellness input to the dose human-factor gain, with provenance (ADR-0049).

    ``value`` is the athlete's report on the 1–10 scale, or ``None`` when no check-in
    exists for the session. Following the ADR-0039 ``neutral_missing`` precedent, an
    unknown input is **labelled**, not silently filled: it contributes the identity
    penalty ``1.0`` (no penalty at all) and carries ``confidence = 0.0``, so a consumer
    can always tell "unknown" from "the athlete reported an average day".
    """

    name: str = Field(description="sleep_quality | life_stress_inverse")
    value: float | None = Field(
        default=None,
        description="Athlete report on the 1–10 scale, or null when unknown.",
    )
    source: str = Field(description="Rung taken: reported | neutral_missing")
    confidence: float = Field(
        default=0.0,
        description="1.0 for a reported value, 0.0 for a labelled neutral_missing.",
    )
    penalty: float = Field(
        default=1.0,
        description="Multiplier this input contributed to the gain; always >= 1.0.",
    )


class HumanFactorGain(BaseModel):
    """The human-factor (wellness) gain applied to the six-axis dose (ADR-0049).

    ``value`` is the product of the per-input penalties, so it lies in ``[1, inf)``: it
    scales the dose **up** and the adaptation contribution **down**. ``source`` names the
    weakest rung any input took, so a dose computed from a real check-in is
    distinguishable from one computed with a labelled neutral for missing wellness.
    """

    value: float = 1.0
    source: str = Field(
        default="neutral_missing",
        description="reported (every input measured) | partial_neutral_missing (some "
        "measured) | neutral_missing (none measured).",
    )
    confidence: float = Field(
        default=0.0,
        description="Mean of the per-input confidences: 1.0 all reported, 0.0 none.",
    )
    model_version: str = ""
    inputs: list[HumanFactorInput] = Field(default_factory=lambda: [])


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

    # ADR-0039: the external intensity that shaped this dose, with full provenance.
    # None on paths that never computed one (kept optional for backward-compat dumps).
    external_intensity: ExternalIntensity | None = None

    # ADR-0049: the wellness gain that scaled this dose, with per-input provenance, so
    # "no check-in" is auditable rather than laundered into a midpoint. None only on
    # legacy dose_snapshot dumps written before this field existed.
    human_factor_gain: HumanFactorGain | None = None
