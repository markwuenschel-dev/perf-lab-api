"""Forward-projection request/response schemas (Phase 7 — goal-anchored program).

The frozen contract the web Simulator renders against: a goal-aware, non-mutating
multi-week projection of the athlete's 8 capacity axes plus readiness/fatigue.
See ``app.services.projection_service`` for the pure trajectory engine and
``app.api.v1.simulate`` for the authed endpoint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intensity = Literal["easy", "balanced", "hard"]
Recovery = Literal["high", "standard", "minimal"]


class ProjectionRequest(BaseModel):
    """Parameters for a forward projection under a hypothetical plan."""

    goal: str = Field(description="TrainingGoal being simulated, e.g. 'Powerlifting'")
    weeks: int = Field(ge=1, le=16, description="Projection horizon in weeks (1..16)")
    weekly_volume: int = Field(
        ge=30, le=90, description="Relative weekly volume index (30..90, matches the web slider)"
    )
    intensity: Intensity = Field(description="Session intensity emphasis")
    recovery: Recovery = Field(description="Recovery / lifestyle support level")


class AxisProjection(BaseModel):
    """Projected trajectory for a single capacity axis under plan + baseline."""

    key: str = Field(description="Capacity axis key, e.g. 'max_strength'")
    label: str = Field(description="Human label, e.g. 'Max strength'")
    start: float = Field(description="Current value (week 0)")
    projected: float = Field(description="Value at week N under this plan")
    baseline: float = Field(description="Value at week N under the maintain plan")
    series: list[float] = Field(description="Length weeks+1 (week 0..N), this plan")
    baseline_series: list[float] = Field(description="Length weeks+1, maintain plan")


class ProjectionResponse(BaseModel):
    """Full projection: 8 axes (canonical order) + readiness + peak fatigue."""

    goal: str
    weeks: int
    axes: list[AxisProjection] = Field(description="Exactly 8, canonical CapacityState.KEYS order")
    readiness_series: list[float] = Field(
        description="Length weeks+1, overall readiness 0..100 under this plan"
    )
    peak_fatigue: float = Field(description="Max fatigue (0..100) over the horizon")
