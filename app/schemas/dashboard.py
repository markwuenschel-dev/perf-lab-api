from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.state import UnifiedStateVector


class KPIValueOut(BaseModel):
    code: str
    name: str
    domain: str
    metric_type: str
    unit: str
    value: float
    confidence: float | None
    computed_at: datetime
    is_dashboard_kpi: bool
    can_affect_prescriber_rules: bool


class AnchorObservationOut(BaseModel):
    benchmark_code: str
    name: str
    domain: str
    is_primary_anchor: bool
    metric_type: str
    unit: str
    raw_value: float
    observed_at: datetime


class DashboardBundleOut(BaseModel):
    """Latest primary-anchor observations plus derived KPI snapshots."""

    kpis: list[KPIValueOut]
    primary_anchors: list[AnchorObservationOut]


class DomainSummaryOut(BaseModel):
    domain: str
    kpis: list[KPIValueOut]
    primary_anchors: list[AnchorObservationOut]


class ReadinessOut(BaseModel):
    state: UnifiedStateVector | None
    kpi_flags: dict[str, Any] = Field(
        default_factory=dict,
        description="Soft signals from KPIs (e.g. elevated run fatigue factor)",
    )


class TrainingLoadMetrics(BaseModel):
    """Acute:chronic workload ratio vs the 0.8-1.3 sweet spot.

    ``acute`` is the 7-day summed load; ``chronic`` is the average *weekly*
    load over 28 days (28-day sum / 4). ``acwr`` = acute / chronic. All three
    are ``None`` when there is insufficient history to compute a meaningful
    baseline (``status == "insufficient"``).
    """

    acwr: float | None = Field(None, description="acute(7d) / chronic(28d avg weekly) load ratio")
    acute: float | None = Field(None, description="7-day summed training load")
    chronic: float | None = Field(None, description="28-day average weekly training load")
    status: Literal["insufficient", "low", "optimal", "high"]
    sweet_spot_low: float = 0.8
    sweet_spot_high: float = 1.3


class AdherenceMetrics(BaseModel):
    """Recent plan adherence and the current training streak."""

    pct: float | None = Field(
        None, description="completed / scheduled over the window, 0-100; None if nothing scheduled"
    )
    streak_days: int = Field(0, description="consecutive days with a completed session / logged workout")
    window_days: int = Field(28, description="length of the adherence window in days")


class OverviewMetrics(BaseModel):
    """Real dashboard tiles: training load / ACWR and adherence / streak."""

    training_load: TrainingLoadMetrics
    adherence: AdherenceMetrics
