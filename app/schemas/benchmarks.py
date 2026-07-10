from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
