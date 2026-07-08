"""Wellness ingestion + readiness scalar schemas (P5 / PDR-0005, ADR-0026).

``WellnessSampleIn``/``Out`` carry acute daily-wellness signals in and out.
``ReadinessScore`` is the single backend-owned readiness number plus the
breakdown of how the modeled state and acute wellness combined to produce it.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Readiness / confidence vocabularies (P8; ADR-0052/ADR-0053) -----------------
ReadinessBand = Literal["low", "moderate", "good", "high"]
ConfidenceBand = Literal["low", "medium", "high"]
ConfidenceStatus = Literal["well_supported", "partial_data", "sparse_data", "stale_data"]
# Report-only in P8 (enforced=False); the prescriber does NOT obey this yet (P13).
RecommendationAuthority = Literal[
    "normal", "conservative", "very_conservative", "assessment_prompt_only"
]

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
    stress: float | None = Field(default=None, ge=0.0, le=10.0, description="0–10, higher = worse")
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
    stress: float | None
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


class SignalSummary(BaseModel):
    """Honesty-ladder buckets over the athlete's logical wellness signals (ADR-0053).

    ``provided`` measured today; ``unknown_today`` tracked but absent today (a gap,
    penalizes confidence, never imputed); ``untracked`` not expected (hidden, no penalty);
    ``stale`` last sample is not fresh; ``estimated`` reserved for a future carry-forward
    promotion (always empty in P8 — ADR-0049 ships clean gaps, not imputation).
    """

    provided: list[str] = Field(default_factory=lambda: [])
    unknown_today: list[str] = Field(default_factory=lambda: [])
    untracked: list[str] = Field(default_factory=lambda: [])
    stale: list[str] = Field(default_factory=lambda: [])
    estimated: list[str] = Field(default_factory=lambda: [])


class RecommendationGate(BaseModel):
    """Advisory recommendation authority derived from confidence (ADR-0052).

    **Report-only in P8:** ``enforced`` is always ``False`` and the prescriber does not
    consume this — confidence cannot gate the plan yet (that is P13). It is displayed and
    logged so P13 inherits shadow history. Distinct from the readiness *score*, which may
    transparently nudge the plan.
    """

    max_recommendation_authority: RecommendationAuthority = "normal"
    message: str | None = None
    enforced: bool = Field(default=False, description="Always False in P8 — see ADR-0052")


class ReadinessConfidence(BaseModel):
    """How well-supported today's readiness estimate is (ADR-0052).

    ``confidence`` answers "how much evidence supports the score", NOT "is readiness high".
    Evidence-coverage over load / wellness-signal coverage / freshness / baseline maturity.
    """

    score: float = Field(..., ge=0.0, le=1.0, description="Evidence-coverage confidence, 0–1")
    band: ConfidenceBand
    status: ConfidenceStatus
    reasons: list[str] = Field(
        default_factory=lambda: [], description="Machine-readable reason codes for explainability"
    )
    signal_summary: SignalSummary = Field(default_factory=SignalSummary)
    recommendation_gate: RecommendationGate = Field(default_factory=RecommendationGate)


class ReadinessScore(BaseModel):
    """The one backend-owned readiness number (PDR-0005) + its breakdown.

    ``score`` is ``None`` only when there is no modeled ``AthleteState`` to anchor against —
    wellness modulates the model, it is not a standalone score. ``confidence`` reports how
    well-supported that score is (ADR-0052) and is report-only in P8.
    """

    score: float | None = Field(
        default=None, description="Combined readiness, 0–100 (100 = fully fresh)"
    )
    band: ReadinessBand | None = Field(
        default=None, description="Coarse band over ``score`` for UI"
    )
    modeled: float | None = Field(
        default=None, description="Modeled-only readiness from fatigue state, 0–100"
    )
    wellness_delta: float = Field(
        default=0.0,
        description="Signed acute-wellness adjustment applied, in 0–100 points (0 if no sample)",
    )
    components: list[ReadinessComponent] = Field(default_factory=lambda: [])
    confidence: ReadinessConfidence | None = None
    wellness_sample: WellnessSampleOut | None = None
    as_of: datetime | None = Field(default=None, description="Timestamp of the modeled state used")
    note: str | None = None
