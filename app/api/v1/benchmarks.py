from datetime import UTC, datetime

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
    StrengthEvidenceCreate,
    StrengthOutcome,
    UnderivedStrengthReportRead,
)
from app.services import (
    assessment_surface_service,
    benchmark_service,
    dashboard_service,
    strength_evidence_service,
)

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
    # P3-pre: an observation dated in the future cannot have happened yet. Accepting it would
    # make it the state head and push every later event before it.
    if body.observed_at is not None:
        observed = body.observed_at if body.observed_at.tzinfo else body.observed_at.replace(tzinfo=UTC)
        if observed > datetime.now(UTC):
            raise HTTPException(
                status_code=422,
                detail="observed_at is in the future. Send the time the test was performed, or omit it.",
            )
    try:
        return await benchmark_service.create_observation(db, current_user.id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/strength-evidence", response_model=StrengthOutcome)
async def post_strength_evidence(
    body: StrengthEvidenceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StrengthOutcome:
    """Record an athlete-reported strength observation for a canonical lift (S2, P4-3).

    Evidence only: no workout is created and no training dose is applied. The server
    derives the value semantics, the estimate for a reported set, and whether the
    observation may ever size a prescribed load.

    A reported set whose effort is unknown, or whose RPE and RIR disagree, yields no
    estimate: it is kept as a report (``outcome = report_only``) and changes nothing else.

    **Retries.** With ``idempotency_key``, the same key and payload return the first outcome
    and record nothing again; the same key with a different payload is 422. Without a key a
    submission is never treated as a retry.

    **Supersession.** ``supersedes_report_id`` must name one of the athlete's reports (404
    otherwise) that is not yet superseded (409, with the id of the observation that superseded
    it — not proof that this submission succeeded); the submission must be the same performance
    (a reported set of the same lift, load, reps and performance time) with an effort that
    yields an estimate (400)."""
    try:
        return await strength_evidence_service.record_strength_evidence(db, current_user.id, body)
    except strength_evidence_service.ReportNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except strength_evidence_service.ReportAlreadySuperseded as e:
        raise HTTPException(status_code=409, detail={
            "message": str(e),
            "superseded_by_observation_id": e.superseded_by_observation_id,
        }) from e
    except strength_evidence_service.SubmissionKeyReused as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/strength-reports", response_model=list[UnderivedStrengthReportRead])
async def get_strength_reports(
    benchmark_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UnderivedStrengthReportRead]:
    """The athlete's active strength reports that yielded no estimate (P4-3) — superseded
    ones are left out, and stay readable by id — most recently performed first, undated last."""
    return await strength_evidence_service.list_strength_reports(
        db, current_user.id, benchmark_code=benchmark_code, limit=limit
    )


@router.get("/strength-reports/{report_id}", response_model=UnderivedStrengthReportRead)
async def get_strength_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UnderivedStrengthReportRead:
    """One of the athlete's strength reports that yielded no estimate, superseded or not;
    404 for any other."""
    try:
        return await strength_evidence_service.get_strength_report(db, current_user.id, report_id)
    except strength_evidence_service.ReportNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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
