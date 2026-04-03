"""Structured session intent for constraint validation (not free text)."""

from typing import Literal

from pydantic import BaseModel, Field

IntensityBand = Literal["low", "moderate", "high", "max"]


class SessionDraft(BaseModel):
    """
    Normalized draft inferred from prescription + goal + template.
    Used by validate_session — not exposed directly on API v1.
    """

    session_kind: str = Field(
        ...,
        description="e.g. recovery, aerobic_base, olympic_technique, max_strength",
    )
    primary_modality: str = Field(
        default="mixed",
        description="Dominant modality tag for constraint domains",
    )
    intensity_band: IntensityBand = "moderate"
    technical_emphasis: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="0–1: skill/technique priority",
    )
    metabolic_emphasis: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="0–1: glycolytic / metcon load",
    )
    neural_emphasis: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="0–1: CNS / high-velocity demand",
    )
    volume_load_proxy: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Rough session volume / duration normalized",
    )
    max_reps_per_set_cap: int | None = Field(
        None,
        description="Domain cap e.g. Olympic lifts ≤5",
    )
    zone2_fraction_target: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Target fraction Z2 for aerobic-dominant goals",
    )
