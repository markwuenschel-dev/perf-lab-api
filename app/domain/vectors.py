"""
Core domain vectors for the athlete state model.

These are the fundamental typed structures for the digital twin.
They are the single source of truth for the mathematical model
and should be preferred for all internal engine logic.

See PROJECT_AGENT_BRIEF.md for the long-term vision.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal

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


# Weak-prior seed: high uncertainty about an un-measured capacity axis. A benchmark
# shrinks this; time grows it. Relative scale — see ADR-0036.
SEED_CAPACITY_VARIANCE = 1.0


class CapacityConfidence(BaseModel):
    """Per-axis model uncertainty about ``CapacityState``, as a variance proxy.

    Higher variance ⇒ the model is less sure ⇒ a benchmark corrects that axis more
    (scalar Kalman gain). The seed prior is high-variance — a weak prior that yields
    to data — benchmarks shrink it and time grows it. Tracked for capacity axes only
    (fatigue/tissue are transient, re-driven each session). See ADR-0036; the scalar
    here generalizes to the EKF's covariance later (ADR-0015).
    """

    aerobic: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    glycolytic: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    max_strength: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    hypertrophy: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    power: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    skill: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    mobility: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)
    work_capacity: float = Field(SEED_CAPACITY_VARIANCE, ge=0.0)

    KEYS: ClassVar[tuple[str, ...]] = CapacityState.KEYS


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
    """D_t — session stress dose in six dimensions (non-negative)."""

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
    Per-session adaptation signal by capacity axis.

    Computed by the dose engine from aggregated phi_adapt vectors and session
    load. Consumed by state_update to drive explicit capacity gains.
    """
    aerobic: float = Field(0.0, ge=0.0)
    glycolytic: float = Field(0.0, ge=0.0)
    max_strength: float = Field(0.0, ge=0.0)
    hypertrophy: float = Field(0.0, ge=0.0)
    power: float = Field(0.0, ge=0.0)
    skill: float = Field(0.0, ge=0.0)
    mobility: float = Field(0.0, ge=0.0)
    work_capacity: float = Field(0.0, ge=0.0)

    KEYS: ClassVar[tuple[str, ...]] = (
        "aerobic", "glycolytic", "max_strength", "hypertrophy",
        "power", "skill", "mobility", "work_capacity",
    )

    def scaled(self, factor: float) -> AdaptationContribution:
        f = max(0.0, factor)
        return AdaptationContribution(
            aerobic=self.aerobic * f,
            glycolytic=self.glycolytic * f,
            max_strength=self.max_strength * f,
            hypertrophy=self.hypertrophy * f,
            power=self.power * f,
            skill=self.skill * f,
            mobility=self.mobility * f,
            work_capacity=self.work_capacity * f,
        )


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
        return {k: max(0.0, float(x)) for k, x in v.items()}


class EnergyMix(BaseModel):
    """Relative energy system emphasis for an exercise (sums to ~1)."""
    aerobic: float = Field(0.33, ge=0.0, le=1.0)
    glycolytic: float = Field(0.33, ge=0.0, le=1.0)
    alactic: float = Field(0.34, ge=0.0, le=1.0)
