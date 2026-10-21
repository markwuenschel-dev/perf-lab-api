import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.logic import vo2_estimate as vo2


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
    idempotency_key: uuid.UUID | None = Field(
        None,
        description="Names one logical submission (P4-3). A retry after an uncertain failure "
        "resends the same key with the same payload and gets the first outcome back, with "
        "nothing recorded again. The same key with a different payload is refused (422). An "
        "edited payload, or another identical set recorded on purpose, needs a new key. "
        "Optional: a submission without a key is never recognized as a retry, and neither is "
        "any submission made before keys existed.",
    )
    supersedes_report_id: int | None = Field(
        None,
        ge=1,
        description="The id of one of the athlete's underived strength reports that this "
        "submission replaces (P4-3). The submission must yield an estimate for the same lift "
        "(otherwise 400) and the report must not already be superseded (otherwise 409). "
        "Supersession happens only when named here, never inferred.",
    )


UnderivedReason = Literal["effort_unknown", "effort_conflict"]


class UnderivedStrengthReportRead(BaseModel):
    """A reported set the server recorded but derived nothing from (P4-3): its effort is
    unknown (``effort_unknown``) or its RPE and RIR disagree (``effort_conflict``)."""

    id: int
    benchmark_code: str
    method: Literal["rep_set"]
    collection_mode: str
    performed_at: datetime | None
    load_kg: float
    reps: int
    rpe: float | None
    rir: float | None
    reason: UnderivedReason
    created_at: datetime
    superseded_by_observation_id: int | None
    superseded_at: datetime | None


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
    # P3-pre (alembic a054). `applied`: its state effect was evaluated against the current
    # state, in time order; a state row is written only if capacity changed. `record_only`:
    # dated before the athlete's current state, so kept as evidence with the state update
    # omitted. Null: it carried no state authority, or it predates dispositions.
    state_disposition: Literal["applied", "record_only"] | None = None
    state_disposition_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class StrengthOutcome(BaseModel):
    """What happened to one strength report (P4-3), for a standalone submission and for each
    report in onboarding.

    * ``recorded``: it became strength evidence (``observation``).
    * ``report_only``: it was recorded as a report (``report``) and no estimate was derived;
      ``reason`` says why.
    * ``already_recorded``: an identical report was already recorded (onboarding's retry guard);
      the record it matched is returned as it is. A matched observation carries its stored value
      (a set recorded before P4-3 is not reclassified); a matched report carries its ``reason``.

    ``value_available`` is whether the record carries a value: true for an observation, false for
    a report.
    """

    outcome: Literal["recorded", "report_only", "already_recorded"]
    reason: UnderivedReason | None = None
    value_available: bool
    observation: BenchmarkObservationRead | None = None
    report: UnderivedStrengthReportRead | None = None

    @model_validator(mode="after")
    def _one_record(self) -> "StrengthOutcome":
        if (self.observation is None) == (self.report is None):
            raise ValueError("an outcome carries exactly one of observation or report")
        if self.outcome == "recorded" and (self.observation is None or not self.value_available):
            raise ValueError("a recorded outcome carries an observation with a value")
        if self.outcome == "report_only" and (self.report is None or self.reason is None):
            raise ValueError("a report_only outcome carries a report and its reason")
        if self.report is not None and self.value_available:
            raise ValueError("a report carries no value")
        if self.observation is not None and (not self.value_available or self.reason is not None):
            raise ValueError("an observation carries its value and no underived reason")
        return self


class RecomputeDerivedResponse(BaseModel):
    snapshots_written: int
    codes_computed: list[str]


class RunTimeTrialReport(BaseModel):
    """A 1.5 mi time trial the athlete ran (B2): what they did, and the model's inputs.

    The time is required; it is the record. Body mass and the model's sex input are needed only
    for the display-only VO₂max estimate, and are never defaulted or inferred: leave them out and
    the run is still saved, without an estimate. ``performed_on`` (the athlete's calendar date)
    with the profile's date of birth gives the age at performance, which decides only whether the
    result is labelled as within the study's age range.
    """

    model_config = ConfigDict(extra="forbid")

    elapsed_seconds: float = Field(
        ..., ge=180, le=14400, allow_inf_nan=False,
        description="The 1.5 mi time, in seconds (3 min to 4 h).",
    )
    performed_on: date | None = Field(
        None, description="The athlete's calendar date of the run. Omitted means unknown."
    )
    body_mass: float | None = Field(
        None, gt=0, allow_inf_nan=False,
        description="Body mass as the athlete entered it, in body_mass_unit: 25-300 kg "
        "(55.2-661.3 lb).",
    )
    body_mass_unit: Literal["kg", "lb"] | None = None
    sex_for_estimate: Literal["female", "male"] | None = Field(
        None,
        description="The estimate model's sex input, used for nothing else and stored only on "
        "this record. Omitted means no estimate.",
    )

    @model_validator(mode="after")
    def _mass_has_a_unit_and_is_plausible(self) -> "RunTimeTrialReport":
        if (self.body_mass is None) != (self.body_mass_unit is None):
            raise ValueError("body_mass and body_mass_unit go together")
        if self.body_mass is not None and self.body_mass_unit is not None:
            low, high = vo2.BODY_MASS_KG_BOUNDS
            kg = vo2.body_mass_kg(self.body_mass, self.body_mass_unit)
            if not low <= kg <= high:
                raise ValueError(
                    f"body_mass must be between {low:g} and {high:g} kg "
                    f"({vo2.BODY_MASS_LB_RANGE[0]:g}-{vo2.BODY_MASS_LB_RANGE[1]:g} lb); got "
                    f"{self.body_mass:g} {self.body_mass_unit}"
                )
        return self


Vo2EstimateStatus = Literal["estimated", "not_estimated"]
Vo2NotEstimatedReason = Literal[
    "coefficients_unverified", "missing_inputs", "non_positive_result", "unsupported_model"
]
Vo2PopulationStatus = Literal["within_study_age_range", "outside_study_age_range", "age_unknown"]


class Vo2EstimateRead(BaseModel):
    """A saved 1.5 mi time trial and its display-only VO₂max estimate (B2).

    Recomputed from the inputs captured on the record itself, never from the live profile.
    ``used_for_plan`` is always false: the record has no capacity, prescription or attainment
    authority.
    """

    observation_id: int
    benchmark_code: str
    performed_on: date | None
    recorded_at: datetime
    elapsed_seconds: float
    status: Vo2EstimateStatus
    reason: Vo2NotEstimatedReason | None
    missing_inputs: list[str]
    vo2max_ml_kg_min: float | None
    units: Literal["ml/kg/min"] = "ml/kg/min"
    headline: str
    population_status: Vo2PopulationStatus
    extrapolated: bool
    population_label: str
    see_ml_kg_min: float
    model_id: str
    model_version: int
    used_for_plan: Literal[False] = False
