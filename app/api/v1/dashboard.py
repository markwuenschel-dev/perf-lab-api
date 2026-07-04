from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.dashboard import (
    AnchorObservationOut,
    DashboardBundleOut,
    DomainSummaryOut,
    KPIValueOut,
    OverviewMetrics,
    ReadinessOut,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/kpis", response_model=DashboardBundleOut)
async def get_dashboard_kpis(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardBundleOut:
    kpis_raw, anchors_raw = await dashboard_service.dashboard_kpis_bundle(db, current_user.id)
    return DashboardBundleOut(
        kpis=[KPIValueOut(**k) for k in kpis_raw],
        primary_anchors=[AnchorObservationOut(**a) for a in anchors_raw],
    )


@router.get("/domain-summary", response_model=DomainSummaryOut)
async def get_domain_summary(
    domain: str = Query(..., min_length=2, max_length=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DomainSummaryOut:
    kpis_raw, anchors_raw = await dashboard_service.domain_summary(
        db, current_user.id, domain=domain
    )
    return DomainSummaryOut(
        domain=domain,
        kpis=[KPIValueOut(**k) for k in kpis_raw],
        primary_anchors=[AnchorObservationOut(**a) for a in anchors_raw],
    )


@router.get("/overview", response_model=OverviewMetrics)
async def get_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OverviewMetrics:
    """Real Overview tiles: training load / ACWR and adherence / streak.

    Returns null/insufficient figures rather than erroring for users with
    little or no history.
    """
    return await dashboard_service.overview_metrics(db, current_user.id)


@router.get("/readiness", response_model=ReadinessOut)
async def get_readiness(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReadinessOut:
    state, flags = await dashboard_service.readiness_payload(db, current_user.id)
    return ReadinessOut(state=state, kpi_flags=flags)
