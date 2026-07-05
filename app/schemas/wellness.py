"""Wellness ingestion + readiness scalar schemas (P5 / PDR-0005, ADR-0026).

``WellnessSampleIn``/``Out`` carry acute daily-wellness signals in and out.
``ReadinessScore`` is the single backend-owned readiness number plus the
breakdown of how the modeled state and acute wellness combined to produce it.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Soreness/mood are assumed on a 0–10 scale (soreness: higher = more sore;
# mood: higher = better). The readiness combine rule encodes the same scales.


class WellnessSampleIn(BaseModel):
    """One acute daily-wellness reading to ingest (manual entry or provider pull)."""

    date: date_cls
    source: str = Field("manual", max_length=50, description="manual | google_fit | oura | …")
    hrv_ms: float | None = Field(default=None, ge=0.0, description="rMSSD-style HRV (ms)")
    sleep_hours: float | None = Field(default=None, ge=0.0, le=24.0)
    sleep_quality: float | None = Field(default=None, ge=0.0, le=100.0)
    resting_hr: float | None = Field(default=None, ge=0.0, le=250.0)
    soreness: float | None = Field(default=None, ge=0.0, le=10.0, description="0–10, higher = worse")
    mood: float | None = Field(default=None, ge=0.0, le=10.0, description="0–10, higher = better")
    raw: dict[str, Any] | None = Field(default=None, description="Source payload for provenance")


class WellnessSampleOut(BaseModel):
    id: int
    user_id: int
    date: date_cls
    source: str
    hrv_ms: float | None
    sleep_hours: float | None
    sleep_quality: float | None
    resting_hr: float | None
    soreness: float | None
    mood: float | None
    raw: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReadinessComponent(BaseModel):
    """How a single acute-wellness signal nudged readiness."""

    signal: str
    value: float
    baseline: float = Field(..., description="Personal rolling baseline, or the default anchor")
    contribution: float = Field(
        ...,
        description="Direction-signed, clamped deviation in [-1, 1] (1 = a full unit better than baseline)",
    )


class ReadinessScore(BaseModel):
    """The one backend-owned readiness number (PDR-0005) + its breakdown.

    ``readiness`` is ``None`` only when there is no modeled ``AthleteState`` to
    anchor against — wellness modulates the model, it is not a standalone score.
    """

    readiness: float | None = Field(
        default=None, description="Combined readiness, 0–100 (1 = fully fresh)"
    )
    modeled: float | None = Field(
        default=None, description="Modeled-only readiness from fatigue state, 0–100"
    )
    wellness_delta: float = Field(
        default=0.0,
        description="Signed acute-wellness adjustment applied, in 0–100 points (0 if no sample)",
    )
    components: list[ReadinessComponent] = Field(default_factory=lambda: [])
    wellness_sample: WellnessSampleOut | None = None
    as_of: datetime | None = Field(default=None, description="Timestamp of the modeled state used")
    note: str | None = None
