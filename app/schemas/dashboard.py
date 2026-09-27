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
    """Acute:chronic workload ratio against a 0.8-1.3 reference band.

    The band is an unvalidated heuristic: a training-load rule of thumb that this engine has
    never checked against injury outcomes. It is not an injury-risk predictor; ``status`` only
    says where the ratio sits relative to the band.

    ``acute`` is the 7-day summed load; ``chronic`` is the average *weekly*
    load over 28 days (28-day sum / 4). ``acwr`` = acute / chronic. All three
    are ``None`` when there is insufficient history to compute a meaningful
    baseline (``status == "insufficient"``).
    """

    acwr: float | None = Field(
        None,
        description=(
            "acute(7d) / chronic(28d avg weekly) load ratio. Descriptive only, not a "
            "validated injury-risk predictor"
        ),
    )
    acute: float | None = Field(None, description="7-day summed training load")
    chronic: float | None = Field(None, description="28-day average weekly training load")
    status: Literal["insufficient", "low", "optimal", "high"] = Field(
        description=(
            "Where acwr sits relative to the heuristic 0.8-1.3 band; 'optimal' means inside "
            "the band, not a validated safe range"
        ),
    )
    sweet_spot_low: float = Field(0.8, description="Lower edge of the unvalidated heuristic band")
    sweet_spot_high: float = Field(1.3, description="Upper edge of the unvalidated heuristic band")


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
