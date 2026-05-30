"""
Core domain vectors for the athlete state model.

These are the fundamental typed structures for the digital twin.
They are the single source of truth for the mathematical model
and should be preferred for all internal engine logic.

See PROJECT_AGENT_BRIEF.md for the long-term vision.
"""
from __future__ import annotations

from typing import ClassVar, Literal, Mapping

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Type Aliases (for documentation and future stricter typing)
# =============================================================================

CapacityKey = Literal[
    "aerobic", "glycolytic", "max_strength", "hypertrophy",
    "power", "skill", "mobility", "work_capacity"
]

FatigueKey = Literal[
    "cns", "muscular", "metabolic", "structural", "tendon", "grip"
]

TissueKey = Literal[
    "shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger"
]


# =============================================================================
# Primary State Vectors
# =============================================================================

class CapacityState(BaseModel):
    """X_t — capacity / adaptation ceiling components (higher = better)."""

    aerobic: float = Field(300.0, ge=0.0, description="Aerobic / CS–VO2 proxy")
    glycolytic: float = Field(50.0, ge=0.0, description="Glycolytic / W′-style reserve")
    max_strength: float = Field(100.0, ge=0.0)
    hypertrophy: float = Field(50.0, ge=0.0)
    power: float = Field(50.0, ge=0.0)
    skill: float = Field(50.0, ge=0.0)
    mobility: float = Field(50.0, ge=0.0)
    work_capacity: float = Field(50.0, ge=0.0)

    KEYS: ClassVar[tuple[str, ...]] = (
        "aerobic", "glycolytic", "max_strength", "hypertrophy",
        "power", "skill", "mobility", "work_capacity",
    )


class FatigueState(BaseModel):
    """F_t — multi-component fatigue (0–100). Higher = more fatigued."""

    cns: float = Field(0.0, ge=0.0, le=100.0)
    muscular: float = Field(0.0, ge=0.0, le=100.0)
    metabolic: float = Field(0.0, ge=0.0, le=100.0)
    structural: float = Field(0.0, ge=0.0, le=100.0)
    tendon: float = Field(0.0, ge=0.0, le=100.0)
    grip: float = Field(0.0, ge=0.0, le=100.0)

    KEYS: ClassVar[tuple[str, ...]] = (
        "cns", "muscular", "metabolic", "structural", "tendon", "grip"
    )


class TissueState(BaseModel):
    """T_t — accumulated tissue / structural stress (0–100)."""

    shoulder: float = Field(0.0, ge=0.0, le=100.0)
    elbow: float = Field(0.0, ge=0.0, le=100.0)
    wrist: float = Field(0.0, ge=0.0, le=100.0)
    lumbar: float = Field(0.0, ge=0.0, le=100.0)
    hip: float = Field(0.0, ge=0.0, le=100.0)
    knee: float = Field(0.0, ge=0.0, le=100.0)
    ankle: float = Field(0.0, ge=0.0, le=100.0)
    finger: float = Field(0.0, ge=0.0, le=100.0)

    KEYS: ClassVar[tuple[str, ...]] = (
        "shoulder", "elbow", "wrist", "lumbar", "hip",
        "knee", "ankle", "finger",
    )


# =============================================================================
# Dose & Adaptation Vectors
# =============================================================================

class StressDoseSix(BaseModel):
    """D_t — session stress dose decomposed into six primary dimensions."""

    volume: float = Field(0.0, ge=0.0)
    intensity: float = Field(0.0, ge=0.0)
    density: float = Field(0.0, ge=0.0)
    impact: float = Field(0.0, ge=0.0)
    skill: float = Field(0.0, ge=0.0)
    metabolic: float = Field(0.0, ge=0.0)

    KEYS: ClassVar[tuple[str, ...]] = (
        "volume", "intensity", "density", "impact", "skill", "metabolic"
    )

    @classmethod
    def zeros(cls) -> StressDoseSix:
        return cls()

    def scaled(self, factor: float) -> StressDoseSix:
        f = max(0.0, factor)
        return StressDoseSix(
            volume=self.volume * f,
            intensity=self.intensity * f,
            density=self.density * f,
            impact=self.impact * f,
            skill=self.skill * f,
            metabolic=self.metabolic * f,
        )


class AdaptationContribution(BaseModel):
    """
    Per-capacity adaptation signal produced by a session.

    This is the 'B·φ_adapt(D_t)' term in the state update equations.
    """
    aerobic: float = Field(0.0, ge=0.0)
    glycolytic: float = Field(0.0, ge=0.0)
    max_strength: float = Field(0.0, ge=0.0)
    hypertrophy: float = Field(0.0, ge=0.0)
    power: float = Field(0.0, ge=0.0)
    skill: float = Field(0.0, ge=0.0)
    mobility: float = Field(0.0, ge=0.0)
    work_capacity: float = Field(0.0, ge=0.0)


# =============================================================================
# Exercise Mapping Vectors
# =============================================================================

class PhiVectors(BaseModel):
    """Exercise-level mapping weights (φ vectors)."""
    adapt: dict[str, float] = Field(default_factory=dict)
    fatigue: dict[str, float] = Field(default_factory=dict)
    tissue: dict[str, float] = Field(default_factory=dict)

    @field_validator("adapt", "fatigue", "tissue")
    @classmethod
    def non_negative_values(cls, v: Mapping[str, float]) -> dict[str, float]:
        return {k: max(0.0, float(x)) for k, v in v.items()}


class EnergyMix(BaseModel):
    """Relative energy system emphasis for an exercise (sums to ~1)."""
    aerobic: float = Field(0.33, ge=0.0, le=1.0)
    glycolytic: float = Field(0.33, ge=0.0, le=1.0)
    alactic: float = Field(0.34, ge=0.0, le=1.0)
