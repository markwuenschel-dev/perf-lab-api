from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.assessment import AssessmentSurfaceRead
from app.schemas.benchmarks import (
    BenchmarkDefinitionRead,
    BenchmarkObservationCreate,
    BenchmarkObservationRead,
    RecomputeDerivedResponse,
)
from app.services import assessment_surface_service, benchmark_service, dashboard_service

router = APIRouter(prefix="/benchmarks", tags=["Benchmarks"])


@router.get("/definitions", response_model=list[BenchmarkDefinitionRead])
async def get_benchmark_definitions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BenchmarkDefinitionRead]:
    rows = await benchmark_service.list_definitions(db)
    return [BenchmarkDefinitionRead.model_validate(r) for r in rows]


@router.get("/assessment-surface", response_model=AssessmentSurfaceRead)
async def get_assessment_surface(
    mode: str = Query("onramp", pattern="^(onramp|retest)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssessmentSurfaceRead:
    """The one domain-filtered assessment surface (ADR-0047), with a measurement-debt
    ranking of which benchmarks to assess next. ``mode`` is product framing only —
    onboarding (``onramp``) vs ongoing (``retest``); the data path is identical."""
    try:
        return await assessment_surface_service.build_assessment_surface(
            db, current_user.id, mode
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/observations", response_model=BenchmarkObservationRead)
async def post_benchmark_observation(
    body: BenchmarkObservationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BenchmarkObservationRead:
    try:
        return await benchmark_service.create_observation(db, current_user.id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/observations", response_model=list[BenchmarkObservationRead])
async def get_benchmark_observations(
    benchmark_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BenchmarkObservationRead]:
    return await benchmark_service.list_observations(
        db,
        current_user.id,
        benchmark_code=benchmark_code,
        limit=limit,
    )


@router.post("/recompute-derived", response_model=RecomputeDerivedResponse)
async def post_recompute_derived(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecomputeDerivedResponse:
    n, codes = await dashboard_service.recompute_derived_metrics(db, current_user.id)
    return RecomputeDerivedResponse(snapshots_written=n, codes_computed=codes)
