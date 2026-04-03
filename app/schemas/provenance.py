"""Provenance metadata for prescriptions, exercises, and templates."""

from typing import Literal

from pydantic import BaseModel, Field


SourceType = Literal["literature", "coach_system", "derived"]
EvidenceLevel = Literal["high", "moderate", "heuristic"]


class TrainingPrimitive(BaseModel):
    """Traceable building block: literature, named coaching idea, or model-derived."""

    id: str
    name: str
    domain: str = Field(
        ...,
        description="e.g. powerlifting, weightlifting, running, conditioning",
    )
    source_type: SourceType
    source_name: str = Field(
        ...,
        description='e.g. "Banister impulse–response", "Hinshaw aerobic system"',
    )
    evidence_level: EvidenceLevel
    description: str


class ProvenanceRef(BaseModel):
    """Lightweight attachment for API payloads."""

    primitive_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
