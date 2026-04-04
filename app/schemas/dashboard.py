from datetime import datetime
from typing import Any

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
