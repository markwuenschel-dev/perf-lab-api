from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkDefinitionRead(BaseModel):
    id: int
    code: str
    name: str
    domain: str
    metric_type: str
    unit: str
    is_primary_anchor: bool
    is_derived_only: bool
    is_validator_only: bool
    description: str | None
    protocol_summary: str | None
    better_direction: str
    observation_weight: float
    state_targets: list[str] | None
    fatigue_targets: list[str] | None
    tissue_targets: list[str] | None

    model_config = ConfigDict(from_attributes=True)


class BenchmarkObservationCreate(BaseModel):
    benchmark_code: str = Field(..., description="Stable code from benchmark_definitions")
    raw_value: float
    secondary_value: float | None = None
    normalized_value: float | None = None
    observed_at: datetime | None = None
    bodyweight_kg: float | None = None
    rpe: float | None = None
    heart_rate_avg: float | None = None
    heart_rate_drift_pct: float | None = None
    notes: str | None = None
    protocol_metadata: dict[str, Any] | None = None
    validity_status: str = Field(default="valid")
    source: str = Field(default="manual")

    # Evidence authority + provenance (ADR-0055). Optional — the service resolves
    # sensible defaults from `source` (manual/benchmark → capacity-authoritative;
    # workout_extraction → estimated, non-regressing). Capacity authority is decided
    # fail-closed in the service via app.logic.strength_evidence, not by the caller.
    evidence_type: str | None = None
    value_semantics: str | None = None
    observation_model: str | None = None
    model_version: str | None = None
    affects_capacity: bool | None = None
    can_regress_capacity: bool | None = None
    affects_prescription: bool | None = None

    # Policy-derived capacity authority (ADR-0058). All optional — the service
    # derives source_type/collection_mode from `source` when omitted, and resolves
    # capacity_effect from the five provenance dimensions (a caller may only ever
    # *narrow* via requested_capacity_effect, never elevate).
    source_type: str | None = None
    collection_mode: str | None = None
    requested_capacity_effect: str | None = None
    confidence_source: str | None = None
    confidence_model_version: str | None = None
    observation_weight: float | None = None
    confidence: float | None = None
    exercise_id: int | None = None
    workout_log_id: int | None = None
    set_log_id: int | None = None
    reps: int | None = None
    load_kg: float | None = None
    rir: float | None = None
    formula: str | None = None
    effort_fidelity: str | None = None


StrengthEvidenceMethod = Literal["tested_max", "rep_set", "estimate"]

#: Request fields that once carried canonical-lift strength as bare numbers. A strength fact
#: for these lifts is now characterized evidence (S2): method, value or set, and date.
RETIRED_BARE_LIFT_FIELDS: tuple[str, ...] = ("squat_1rm_kg", "bench_1rm_kg", "deadlift_1rm_kg")


def refuse_bare_canonical_lift_numbers(data: Any, *, instead: str) -> Any:
    """Before-validator: an uncharacterized canonical-lift number is refused, loudly.

    Silently ignoring it would drop a fact the athlete gave; accepting it would create a
    second write authority for strength, beside the characterized-evidence path.
    """
    # Narrow a separate name, so `data` itself is returned exactly as received.
    received: object = data
    sent = [
        name for name in RETIRED_BARE_LIFT_FIELDS if isinstance(received, dict) and name in received
    ]
    if sent:
        raise ValueError(
            f"{', '.join(sent)} is no longer accepted: squat, bench and deadlift are "
            f"recorded as characterized strength evidence — {instead}"
        )
    return data


class StrengthReport(BaseModel):
    """What an athlete reports about one canonical lift (S2).

    The client states WHAT HAPPENED: which lift, how the number was obtained, when, and —
    for a set — its load, reps, and effort. It cannot state value semantics, evidence type,
    prescription permission, or a computed e1RM; ``strength_evidence_service`` derives those
    with the same shared qualification logic workout extraction uses. Unknown keys are
    rejected, so none of those can be smuggled in. Recording a report never creates a
    workout or applies a training dose.

    A "tested max" remains the athlete's report of a directly measured performance, not an
    independently verified one.
    """

    model_config = ConfigDict(extra="forbid")

    benchmark_code: str = Field(..., description="The e1RM benchmark code of a canonical lift.")
    method: StrengthEvidenceMethod = Field(
        ...,
        description="tested_max: a 1-rep max the athlete performed. rep_set: a set the athlete "
        "performed, described by load_kg and reps (plus effort when known). estimate: the "
        "athlete's estimate, retained as reported information and never a prescription basis.",
    )
    performed_at: datetime | None = Field(
        None,
        description="When the lift was performed. Omitted means unknown: the observation is "
        "recorded but never sizes a prescribed load.",
    )
    value_kg: float | None = Field(
        None, gt=0, allow_inf_nan=False, description="tested_max / estimate: the weight in kg."
    )
    load_kg: float | None = Field(
        None, gt=0, allow_inf_nan=False, description="rep_set: the set's load in kg."
    )
    reps: int | None = Field(None, ge=1, description="rep_set: repetitions completed.")
    rpe: float | None = Field(None, ge=1, le=10, allow_inf_nan=False)
    rir: float | None = Field(None, ge=0, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def _fields_match_the_method(self) -> "StrengthReport":
        set_fields = {"load_kg": self.load_kg, "reps": self.reps, "rpe": self.rpe, "rir": self.rir}
        if self.method == "rep_set":
            if self.load_kg is None or self.reps is None:
                raise ValueError("a reported set needs load_kg and reps")
            if self.value_kg is not None:
                raise ValueError("a reported set is described by load_kg and reps, not value_kg")
            return self
        if self.value_kg is None:
            raise ValueError(f"{self.method} needs value_kg")
        supplied = sorted(name for name, value in set_fields.items() if value is not None)
        if supplied:
            raise ValueError(f"{self.method} does not take set fields: {', '.join(supplied)}")
        return self


class StrengthEvidenceCreate(StrengthReport):
    """A strength report submitted on its own — from Assess, or a Settings strength edit."""

    collection_mode: Literal["onboarding_onramp", "retest"] = "retest"


class BenchmarkObservationRead(BaseModel):
    id: int
    user_id: int
    benchmark_definition_id: int
    benchmark_code: str
    observed_at: datetime
    raw_value: float
    secondary_value: float | None
    normalized_value: float | None
    validity_status: str
    source: str

    model_config = ConfigDict(from_attributes=True)


class RecomputeDerivedResponse(BaseModel):
    snapshots_written: int
    codes_computed: list[str]
