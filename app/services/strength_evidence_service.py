"""Athlete-reported strength for the canonical lifts — the one write authority (S2).

An athlete reports a strength observation — a tested 1-rep max, a set they performed (load ×
reps, with effort when known), or an estimate — from onboarding, Assess, or a Settings strength
edit. Every one of those routes comes here (S2 decisions 2 and 3). This module turns the report
into one benchmark observation, derives the profile's value for that lift from the evidence, and
does nothing more: it never creates a workout, never applies a training dose, and never grants
authority the report does not support.

The server, not the client, decides what the report is:

============  ======================  ===============  =========================================
method        stored value            value semantics  may it size a prescribed load?
============  ======================  ===============  =========================================
tested_max    the reported weight     measured         when dated and inside the freshness window
rep_set       e1RM of the set         estimated        only if the set clears the shared gate
estimate      the reported weight     estimated        never — retained as reported information
============  ======================  ===============  =========================================

Whether a row actually sizes a load is decided at selection time by
``app.logic.prescription_evidence``, which applies the qualifying-set gate to every set-derived
row whatever route recorded it. The ``affects_prescription`` flag written here is permission,
not a verdict: an estimate is refused outright; a reported set is permitted and then qualified
on its stored set, exactly as workout extraction's rows are.

**One fact, one write authority.** ``AthleteProfile.squat_1rm`` / ``bench_1rm`` /
``deadlift_1rm`` are a projection and seed field: this module writes them, derived from the
athlete's reports, and no route writes them as a bare number any more. Only rows this module
wrote can move them, which is decided by ``provenance_operation`` — set by the server — and not
by ``observation_model``, which the generic observations API accepts from clients. Values
written before this model are legacy seeds; they are left alone and never turned into evidence,
because their method and performance date are unknown.

**One transaction per save.** A report's evidence, its state consequences, and the profile value
derived from it commit together or not at all. Onboarding stages its reports inside its own
single transaction and skips a report identical to one onboarding already recorded, so a retry
cannot duplicate one.

A "tested max" remains the athlete's report of a directly measured performance, not an
independently verified one. Its capacity effect is whatever the existing ADR-0058 policy
resolves for that report — this module does not change that policy; under it a reported set
or an estimate reaches at most an upward floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.logic.prescription_evidence import utc_naive
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import AthleteProfile
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.benchmarks import (
    BenchmarkObservationCreate,
    BenchmarkObservationRead,
    StrengthEvidenceCreate,
    StrengthReport,
)
from app.services import benchmark_service

SQUAT_E1RM_CODE = "pl_e1rm_squat"
BENCH_E1RM_CODE = "pl_e1rm_bench"
DEADLIFT_E1RM_CODE = "pl_e1rm_deadlift"

#: Canonical lift -> the AthleteProfile column this module projects that lift's strength onto.
CANONICAL_LIFT_PROFILE_COLUMNS: dict[str, str] = {
    SQUAT_E1RM_CODE: "squat_1rm",
    BENCH_E1RM_CODE: "bench_1rm",
    DEADLIFT_E1RM_CODE: "deadlift_1rm",
}

#: ``observation_model`` of every observation this module writes. Descriptive only: a client can
#: send the same string to the generic observations API, so nothing may trust it as authority.
STRENGTH_REPORT_MODEL = "strength_report_v1"


async def canonical_e1rm_codes(db: AsyncSession) -> set[str]:
    """The e1RM benchmark codes of canonical catalog lifts — the only lifts a report may name."""
    rows = await db.execute(
        select(Exercise.e1rm_benchmark_code)
        .where(Exercise.e1rm_benchmark_code.isnot(None))
        .distinct()
    )
    return {code for (code,) in rows.all() if code}


async def check_reports(
    db: AsyncSession, reports: Sequence[StrengthReport], *, now: datetime | None = None
) -> None:
    """Refuse a report naming a non-canonical lift or a future performance — before any write."""
    if not reports:
        return
    canonical = await canonical_e1rm_codes(db)
    reference = utc_naive(now or datetime.now(UTC))
    for report in reports:
        if (
            report.benchmark_code not in canonical
            or report.benchmark_code not in CANONICAL_LIFT_PROFILE_COLUMNS
        ):
            raise ValueError(
                f"{report.benchmark_code!r} is not the e1RM benchmark of a canonical lift"
            )
        if report.performed_at is not None and utc_naive(report.performed_at) > reference:
            raise ValueError("performed_at is in the future")


def derived_value(report: StrengthReport) -> float:
    """The kilograms a report stands for: a reported set's e1RM (the one shared estimate),
    otherwise the reported weight. The evidence and the state seed both use this."""
    if report.method == "rep_set":
        assert report.load_kg is not None and report.reps is not None  # the schema guarantees it
        return sc.e1rm_from_set(report.load_kg, report.reps)
    assert report.value_kg is not None  # the schema guarantees it for tested_max and estimate
    return report.value_kg


def seed_values(reports: Sequence[StrengthReport]) -> dict[str, float]:
    """Benchmark code -> the derived value a report seeds the initial state with."""
    return {report.benchmark_code: derived_value(report) for report in reports}


async def record_strength_evidence(
    db: AsyncSession,
    user_id: int,
    body: StrengthEvidenceCreate,
    *,
    now: datetime | None = None,
) -> BenchmarkObservationRead:
    """A report submitted on its own (Assess, Settings)."""
    return await record_strength_report(
        db, user_id, body, collection_mode=body.collection_mode, now=now
    )


async def record_strength_report(
    db: AsyncSession,
    user_id: int,
    report: StrengthReport,
    *,
    collection_mode: str,
    now: datetime | None = None,
) -> BenchmarkObservationRead:
    """Record one report and the profile value derived from it, in one transaction."""
    await check_reports(db, [report], now=now)
    staged = await stage_strength_report(db, user_id, report, collection_mode=collection_mode)
    assert staged is not None  # only skip_if_recorded can skip
    await db.commit()
    return await benchmark_service.complete_observation(db, user_id, staged)


async def stage_strength_report(
    db: AsyncSession,
    user_id: int,
    report: StrengthReport,
    *,
    collection_mode: str,
    skip_if_recorded: bool = False,
) -> benchmark_service.StagedObservation | None:
    """Stage a report's evidence and the profile value derived from it in the caller's
    transaction. Does not commit; call :func:`check_reports` first.

    With ``skip_if_recorded``, a report identical to one this writer already recorded for the
    athlete in the same collection mode is not staged again, and ``None`` is returned. That is
    onboarding's retry guard: the server cannot tell a retry whose first attempt committed from
    the same submission sent twice, and neither may add a second report.
    """
    performed_at = utc_naive(report.performed_at) if report.performed_at is not None else None
    observation = _observation_for(report, collection_mode)
    if skip_if_recorded and await _already_recorded(db, user_id, observation, performed_at):
        return None
    staged = await benchmark_service.stage_observation(
        db,
        user_id,
        observation,
        performed_at=performed_at,
        provenance_operation=oa.OP_STRENGTH_REPORT,
    )
    await _stage_projection(db, user_id, report.benchmark_code)
    return staged


def _observation_for(report: StrengthReport, collection_mode: str) -> BenchmarkObservationCreate:
    """The observation a report becomes. Every semantic field is derived here, never taken
    from the client."""
    if report.method == "rep_set":
        return BenchmarkObservationCreate(
            benchmark_code=report.benchmark_code,
            source=se.SOURCE_MANUAL,
            collection_mode=collection_mode,
            observation_model=STRENGTH_REPORT_MODEL,
            raw_value=derived_value(report),
            evidence_type=se.EV_ESTIMATED_FROM_TRAINING_SET,
            value_semantics=se.VS_ESTIMATED,
            affects_prescription=True,
            reps=report.reps,
            load_kg=report.load_kg,
            rpe=report.rpe,
            rir=report.rir,
            formula="epley",
            # A reported set's effort is a statement about that one set.
            effort_fidelity=se.FIDELITY_SET_LEVEL,
        )
    if report.method == "tested_max":
        return BenchmarkObservationCreate(
            benchmark_code=report.benchmark_code,
            source=se.SOURCE_MANUAL,
            collection_mode=collection_mode,
            observation_model=STRENGTH_REPORT_MODEL,
            raw_value=derived_value(report),
            evidence_type=se.EV_DIRECT_MEASUREMENT,
            value_semantics=se.VS_MEASURED,
            affects_prescription=True,
        )
    return BenchmarkObservationCreate(
        benchmark_code=report.benchmark_code,
        source=se.SOURCE_MANUAL,
        collection_mode=collection_mode,
        observation_model=STRENGTH_REPORT_MODEL,
        raw_value=derived_value(report),
        evidence_type=se.EV_REPORTED_ESTIMATE,
        value_semantics=se.VS_ESTIMATED,
        affects_prescription=False,
    )


async def _already_recorded(
    db: AsyncSession,
    user_id: int,
    observation: BenchmarkObservationCreate,
    performed_at: datetime | None,
) -> bool:
    """Whether this writer already recorded exactly this report for the athlete: the same lift,
    collection mode, method (as its evidence type), performance date, and reported numbers."""
    row = BenchmarkObservation
    found = await db.execute(
        select(row.id)
        .join(BenchmarkDefinition, row.benchmark_definition_id == BenchmarkDefinition.id)
        .where(
            row.user_id == user_id,
            BenchmarkDefinition.code == observation.benchmark_code,
            row.provenance_operation == oa.OP_STRENGTH_REPORT,
            row.collection_mode == observation.collection_mode,
            row.evidence_type == observation.evidence_type,
            row.raw_value == observation.raw_value,
            row.performed_at.is_not_distinct_from(performed_at),
            row.load_kg.is_not_distinct_from(observation.load_kg),
            row.reps.is_not_distinct_from(observation.reps),
            row.rpe.is_not_distinct_from(observation.rpe),
            row.rir.is_not_distinct_from(observation.rir),
        )
        .limit(1)
    )
    return found.first() is not None


async def _stage_projection(db: AsyncSession, user_id: int, code: str) -> None:
    """Derive the profile's value for one lift from the athlete's strength reports, in the
    caller's transaction.

    Reads only rows this module wrote (the server-set ``provenance_operation``). The value of
    the report performed most recently — undated reports rank below every dated one — then the
    newest report. The profile column is written here and nowhere else.
    """
    value = (await db.execute(
        select(BenchmarkObservation.raw_value)
        .join(
            BenchmarkDefinition,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == code,
            BenchmarkObservation.provenance_operation == oa.OP_STRENGTH_REPORT,
        )
        .order_by(
            BenchmarkObservation.performed_at.desc().nulls_last(),
            BenchmarkObservation.id.desc(),
        )
        .limit(1)
    )).scalar_one_or_none()
    if value is None:
        return
    profile = await AthleteProfileRepository(db).get_for_user(user_id)
    if profile is None:
        profile = AthleteProfile(user_id=user_id)
        db.add(profile)
    setattr(profile, CANONICAL_LIFT_PROFILE_COLUMNS[code], float(value))
