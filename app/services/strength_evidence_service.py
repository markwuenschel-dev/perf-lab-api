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

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.logic.prescription_evidence import utc_naive
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.strength_report import StrengthSubmissionKey, UnderivedStrengthReport
from app.models.user import AthleteProfile
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.benchmarks import (
    BenchmarkObservationCreate,
    StrengthEvidenceCreate,
    StrengthOutcome,
    StrengthReport,
    UnderivedStrengthReportRead,
)
from app.services import benchmark_service
from app.services.e1rm_estimation import estimate_set_e1rm
from app.services.state_chain_lock import lock_athlete_chain

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


#: Why a reported set yields no estimate (P4-3), stored on its underived report.
REASON_EFFORT_UNKNOWN = "effort_unknown"
REASON_EFFORT_CONFLICT = "effort_conflict"


def underived_reason(method: str, rpe: float | None, rir: float | None) -> str | None:
    """Why a report yields no estimate, or None when it does (P4-3).

    Only a reported set can be underived: its estimate needs a characterized effort, and the
    shared resolver decides whether it has one. Unknown is neither RPE nor RIR; a conflict is
    both, further apart than the resolver's tolerance. A tested max or an estimate carries no
    effort and is never classified this way. Out-of-range effort never gets here (the schema
    refuses it).
    """
    if method != "rep_set":
        return None
    effort = sc.resolve_effort(rpe, rir)
    if effort.conflict:
        return REASON_EFFORT_CONFLICT
    if effort.eff_rpe is None:
        return REASON_EFFORT_UNKNOWN
    return None


def report_underived_reason(report: StrengthReport) -> str | None:
    """:func:`underived_reason` for a submitted report."""
    return underived_reason(report.method, report.rpe, report.rir)


def observation_underived_reason(obs: BenchmarkObservation) -> str | None:
    """:func:`underived_reason` for a stored strength-report observation. A row this module
    wrote with ``reps`` came from a reported set; one written before P4-3 may carry an effort
    that is unknown or conflicting, and it is classified from its stored RPE and RIR."""
    return underived_reason("rep_set" if obs.reps is not None else "other", obs.rpe, obs.rir)


def derived_value(report: StrengthReport) -> float:
    """The kilograms a report stands for: a reported set's e1RM (the one shared estimate),
    otherwise the reported weight. The evidence and the state seed both use this. A report
    that yields no estimate stands for no number at all."""
    reason = report_underived_reason(report)
    if reason is not None:
        raise ValueError(f"this report yields no estimate ({reason})")
    if report.method == "rep_set":
        return _set_estimate(report).value
    assert report.value_kg is not None  # the schema guarantees it for tested_max and estimate
    return report.value_kg


def _set_estimate(report: StrengthReport) -> sc.E1rmEstimate:
    """The estimate a reported set stands for: the same function workout extraction calls."""
    assert report.load_kg is not None and report.reps is not None  # the schema guarantees it
    return estimate_set_e1rm(report.load_kg, report.reps, report.rpe, report.rir)


def seed_values(reports: Sequence[StrengthReport]) -> dict[str, float]:
    """Benchmark code -> the derived value a report seeds the initial state with. A report that
    yields no estimate seeds nothing: the athlete starts as if it had not been given."""
    return {
        report.benchmark_code: derived_value(report)
        for report in reports
        if report_underived_reason(report) is None
    }


class ReportNotFound(Exception):
    """The named report does not exist or is not the athlete's (404)."""


class ReportAlreadySuperseded(Exception):
    """The named report was already superseded (409). Raised only after the report is known to
    be the athlete's, so the superseding id is never another athlete's."""

    def __init__(self, report_id: int, superseded_by_observation_id: int) -> None:
        super().__init__(f"report {report_id} is already superseded")
        self.report_id = report_id
        self.superseded_by_observation_id = superseded_by_observation_id


class SubmissionKeyReused(Exception):
    """An idempotency key already used for a different submission (422). Nothing is written."""


@dataclass(frozen=True)
class StagedStrength:
    """One report's outcome, staged in the caller's open transaction. ``staged`` is set only when
    a new observation was staged: once the caller has committed, pass it to
    :func:`benchmark_service.complete_observation`."""

    outcome: StrengthOutcome
    staged: benchmark_service.StagedObservation | None


async def record_strength_evidence(
    db: AsyncSession,
    user_id: int,
    body: StrengthEvidenceCreate,
    *,
    now: datetime | None = None,
) -> StrengthOutcome:
    """A report submitted on its own (Assess, Settings)."""
    return await record_strength_report(
        db, user_id, body,
        collection_mode=body.collection_mode,
        now=now,
        supersedes_report_id=body.supersedes_report_id,
        idempotency_key=body.idempotency_key,
    )


async def record_strength_report(
    db: AsyncSession,
    user_id: int,
    report: StrengthReport,
    *,
    collection_mode: str,
    now: datetime | None = None,
    supersedes_report_id: int | None = None,
    idempotency_key: uuid.UUID | None = None,
) -> StrengthOutcome:
    """Record one report — as evidence, or as a report when it yields no estimate — and the
    profile value derived from it, in one transaction.

    With an ``idempotency_key``, the key commits together with what it recorded. A later request
    with the same key and the same payload gets the first outcome back, and nothing is staged,
    projected or completed again; the same key with a different payload is refused before any
    write. The chain lock is taken before the key is looked up, so concurrent requests with one
    key are serialized and the second finds the first's key.
    """
    await check_reports(db, [report], now=now)
    await lock_athlete_chain(db, user_id)
    payload: dict[str, Any] | None = None
    digest = ""
    if idempotency_key is not None:
        payload = submission_payload(report, collection_mode, supersedes_report_id)
        digest = _payload_digest(payload)
        prior = await _keyed_outcome(db, user_id, idempotency_key, digest)
        if prior is not None:
            await db.rollback()  # nothing was written; ends the transaction and its lock
            return prior
    result = await stage_strength_report(
        db, user_id, report,
        collection_mode=collection_mode,
        supersedes_report_id=supersedes_report_id,
        now=now,
    )
    if idempotency_key is not None:
        assert payload is not None
        await _stage_key(db, user_id, idempotency_key, payload, digest, result.outcome)
    await db.commit()
    if result.staged is not None:
        await benchmark_service.complete_observation(db, user_id, result.staged)
    return result.outcome


async def stage_strength_report(
    db: AsyncSession,
    user_id: int,
    report: StrengthReport,
    *,
    collection_mode: str,
    skip_if_recorded: bool = False,
    supersedes_report_id: int | None = None,
    now: datetime | None = None,
) -> StagedStrength:
    """Stage one report in the caller's transaction and say what happened to it. Does not
    commit; call :func:`check_reports` first.

    A report that yields no estimate (:func:`report_underived_reason`) becomes an underived
    report: no observation, no projection, no state effect. Any other becomes strength evidence,
    and the profile value for its lift is derived again.

    With ``skip_if_recorded``, a report identical to one this writer already recorded for the
    athlete in the same collection mode — as evidence or as a report, whichever store holds it —
    is not staged again; the outcome is ``already_recorded`` with the record it matched. The
    match is on what was submitted, never on how it is classified now, so a submission recorded
    as evidence before P4-3 is still recognized, and is not recorded a second time as a report.
    That is onboarding's retry guard: the server cannot tell a retry whose first attempt
    committed from the same submission sent twice, and neither may add a second report.

    ``supersedes_report_id`` names an underived report this submission corrects. It must be the
    same performance — a reported set of the same lift, load, reps and performance time — now
    with an effort that yields an estimate; any other performance is a separate recording. The
    report is locked (after the chain lock) and must be the athlete's and not yet superseded.
    The link is staged with the new observation.
    """
    await lock_athlete_chain(db, user_id)
    reason = report_underived_reason(report)
    if supersedes_report_id is not None and reason is not None:
        raise ValueError(
            "a submission that yields no estimate cannot supersede a report "
            f"({reason}); correct the effort information first"
        )
    performed_at = utc_naive(report.performed_at) if report.performed_at is not None else None
    if skip_if_recorded:
        prior = await _already_recorded(db, user_id, report, collection_mode, performed_at)
        if prior is not None:
            return StagedStrength(prior, None)
    if reason is not None:
        row = await _stage_underived_report(
            db, user_id, report, collection_mode, performed_at, reason
        )
        report_read = _report_read(row, report.benchmark_code)
        return StagedStrength(
            StrengthOutcome(
                outcome="report_only",
                reason=report_read.reason,
                value_available=False,
                report=report_read,
            ),
            None,
        )
    target = None
    if supersedes_report_id is not None:
        target = await _lock_supersedable(db, user_id, supersedes_report_id, report, performed_at)
    staged = await benchmark_service.stage_observation(
        db,
        user_id,
        _observation_for(report, collection_mode),
        performed_at=performed_at,
        provenance_operation=oa.OP_STRENGTH_REPORT,
    )
    if target is not None:
        target.superseded_by_observation_id = staged.observation.id
        target.superseded_at = utc_naive(now or datetime.now(UTC))
        await db.flush()  # the link is written with the observation, and undone with it
    await _stage_projection(db, user_id, report.benchmark_code)
    await db.flush()
    await db.refresh(staged.observation)  # a late-event fold may have rewritten its disposition
    return StagedStrength(
        StrengthOutcome(
            outcome="recorded",
            value_available=True,
            observation=benchmark_service.observation_read(
                staged.observation, report.benchmark_code
            ),
        ),
        staged,
    )


def submission_payload(
    report: StrengthReport, collection_mode: str, supersedes_report_id: int | None
) -> dict[str, Any]:
    """The canonical form of a standalone submission — everything but its key — that a reused
    key is compared against. The performance time is naive UTC, so one instant written with two
    offsets is one payload; numbers are floats, so ``100`` and ``100.0`` are one payload."""

    def num(value: float | None) -> float | None:
        return float(value) if value is not None else None

    return {
        "benchmark_code": report.benchmark_code,
        "method": report.method,
        "collection_mode": collection_mode,
        "performed_at": (
            utc_naive(report.performed_at).isoformat() if report.performed_at is not None else None
        ),
        "value_kg": num(report.value_kg),
        "load_kg": num(report.load_kg),
        "reps": report.reps,
        "rpe": num(report.rpe),
        "rir": num(report.rir),
        "supersedes_report_id": supersedes_report_id,
    }


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _keyed_outcome(
    db: AsyncSession, user_id: int, key: uuid.UUID, digest: str
) -> StrengthOutcome | None:
    """The outcome a key first returned, or None for a new key. A key already used for a
    different payload is refused."""
    row = (await db.execute(
        select(StrengthSubmissionKey).where(
            StrengthSubmissionKey.user_id == user_id,
            StrengthSubmissionKey.idempotency_key == key,
        )
    )).scalar_one_or_none()
    if row is None:
        return None
    if row.payload_digest != digest:
        raise SubmissionKeyReused(
            "this idempotency_key was already used for a different submission; "
            "send a new key for a new or edited submission"
        )
    return StrengthOutcome.model_validate(row.outcome)


async def _stage_key(
    db: AsyncSession,
    user_id: int,
    key: uuid.UUID,
    payload: dict[str, Any],
    digest: str,
    outcome: StrengthOutcome,
) -> None:
    """Stage the key with the outcome it returns. The key namespace is per athlete, but the
    table does not check that the record a key names is the athlete's, so that is checked here,
    before anything is stored."""
    if outcome.observation is not None:
        observation_id, report_id = outcome.observation.id, None
        owner = (await db.execute(
            select(BenchmarkObservation.user_id).where(BenchmarkObservation.id == observation_id)
        )).scalar_one_or_none()
    else:
        assert outcome.report is not None  # an outcome carries exactly one record
        observation_id, report_id = None, outcome.report.id
        owner = (await db.execute(
            select(UnderivedStrengthReport.user_id).where(UnderivedStrengthReport.id == report_id)
        )).scalar_one_or_none()
    if owner != user_id:
        raise RuntimeError("refusing to key a submission to a record that is not the athlete's")
    db.add(StrengthSubmissionKey(
        user_id=user_id,
        idempotency_key=key,
        payload_digest=digest,
        payload=payload,
        observation_id=observation_id,
        report_id=report_id,
        outcome=outcome.model_dump(mode="json"),
    ))
    await db.flush()


async def _definition_id(db: AsyncSession, code: str) -> int:
    definition_id = (await db.execute(
        select(BenchmarkDefinition.id).where(BenchmarkDefinition.code == code)
    )).scalar_one_or_none()
    if definition_id is None:
        raise ValueError(f"Unknown benchmark code: {code}")
    return definition_id


async def _stage_underived_report(
    db: AsyncSession,
    user_id: int,
    report: StrengthReport,
    collection_mode: str,
    performed_at: datetime | None,
    reason: str,
) -> UnderivedStrengthReport:
    assert report.load_kg is not None and report.reps is not None  # rep_set: the schema guarantees it
    row = UnderivedStrengthReport(
        user_id=user_id,
        benchmark_definition_id=await _definition_id(db, report.benchmark_code),
        method=report.method,
        collection_mode=collection_mode,
        performed_at=performed_at,
        load_kg=report.load_kg,
        reps=report.reps,
        rpe=report.rpe,
        rir=report.rir,
        reason=reason,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)  # created_at is the server's
    return row


def _report_read(row: UnderivedStrengthReport, benchmark_code: str) -> UnderivedStrengthReportRead:
    return UnderivedStrengthReportRead.model_validate({
        "id": row.id,
        "benchmark_code": benchmark_code,
        "method": row.method,
        "collection_mode": row.collection_mode,
        "performed_at": row.performed_at,
        "load_kg": row.load_kg,
        "reps": row.reps,
        "rpe": row.rpe,
        "rir": row.rir,
        "reason": row.reason,
        "created_at": row.created_at,
        "superseded_by_observation_id": row.superseded_by_observation_id,
        "superseded_at": row.superseded_at,
    })


async def _lock_supersedable(
    db: AsyncSession,
    user_id: int,
    report_id: int,
    report: StrengthReport,
    performed_at: datetime | None,
) -> UnderivedStrengthReport:
    """Lock the report a submission supersedes. Call with the chain lock held. Another athlete's
    report is reported as not found, so nothing about it is revealed; the facts are compared
    only after that. A submission supersedes only the performance the report recorded: the same
    lift, a reported set, the same load, reps and performance time. Only its effort differs."""
    row = (await db.execute(
        select(UnderivedStrengthReport)
        .where(UnderivedStrengthReport.id == report_id)
        .with_for_update()
    )).scalar_one_or_none()
    if row is None or row.user_id != user_id:
        raise ReportNotFound(f"no strength report {report_id}")
    if row.superseded_by_observation_id is not None:
        raise ReportAlreadySuperseded(report_id, row.superseded_by_observation_id)
    if row.benchmark_definition_id != await _definition_id(db, report.benchmark_code):
        raise ValueError("a submission can supersede only a report of the same lift")
    if not (
        report.method == row.method
        and report.load_kg == row.load_kg
        and report.reps == row.reps
        and performed_at == row.performed_at
    ):
        raise ValueError(
            "a submission can supersede only the set the report recorded (same load, reps and "
            "performance time); record a different performance on its own"
        )
    return row


async def list_strength_reports(
    db: AsyncSession, user_id: int, *, benchmark_code: str | None = None, limit: int = 100
) -> list[UnderivedStrengthReportRead]:
    """The athlete's active underived reports — those nothing has superseded — most recently
    performed first (undated last). A superseded report stays readable by id
    (:func:`get_strength_report`)."""
    query = (
        select(UnderivedStrengthReport, BenchmarkDefinition.code)
        .join(
            BenchmarkDefinition,
            UnderivedStrengthReport.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            UnderivedStrengthReport.user_id == user_id,
            UnderivedStrengthReport.superseded_by_observation_id.is_(None),
        )
    )
    if benchmark_code is not None:
        query = query.where(BenchmarkDefinition.code == benchmark_code)
    rows = (await db.execute(
        query.order_by(
            UnderivedStrengthReport.performed_at.desc().nulls_last(),
            UnderivedStrengthReport.id.desc(),
        ).limit(limit)
    )).all()
    return [_report_read(row, code) for row, code in rows]


async def get_strength_report(
    db: AsyncSession, user_id: int, report_id: int
) -> UnderivedStrengthReportRead:
    """One of the athlete's underived reports, superseded or not (its permanent record);
    another athlete's is not found."""
    found = (await db.execute(
        select(UnderivedStrengthReport, BenchmarkDefinition.code)
        .join(
            BenchmarkDefinition,
            UnderivedStrengthReport.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(UnderivedStrengthReport.id == report_id, UnderivedStrengthReport.user_id == user_id)
    )).first()
    if found is None:
        raise ReportNotFound(f"no strength report {report_id}")
    row, code = found
    return _report_read(row, code)


def _observation_for(report: StrengthReport, collection_mode: str) -> BenchmarkObservationCreate:
    """The observation a report becomes. Every semantic field is derived here, never taken
    from the client."""
    if report.method == "rep_set":
        estimate = _set_estimate(report)
        return BenchmarkObservationCreate(
            benchmark_code=report.benchmark_code,
            source=se.SOURCE_MANUAL,
            collection_mode=collection_mode,
            observation_model=STRENGTH_REPORT_MODEL,
            raw_value=estimate.value,
            # A model's point estimate carries no capacity authority; the legacy Epley row keeps
            # its label (P4-2b).
            evidence_type=(
                se.EV_MODELED_ESTIMATE if estimate.modeled else se.EV_ESTIMATED_FROM_TRAINING_SET
            ),
            value_semantics=se.VS_ESTIMATED,
            affects_prescription=True,
            reps=report.reps,
            load_kg=report.load_kg,
            rpe=report.rpe,
            rir=report.rir,
            formula=estimate.formula,
            model_version=estimate.model_version,
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
    report: StrengthReport,
    collection_mode: str,
    performed_at: datetime | None,
) -> StrengthOutcome | None:
    """The outcome for a report this writer already recorded exactly, or None.

    The identity of a report is what the athlete SUBMITTED: the lift, collection mode,
    performance date and reported numbers, and the method. Never what was derived from them, nor
    how it is classified now. A reported set (``reps`` present) is matched on its load, reps, RPE
    and RIR and not on the e1RM computed from them or on the model that computed it, so a retry
    of a submission recorded under another formula (Epley before the chart, the chart after) is
    recognised and adds nothing: no second observation, no projection change, no re-seed. The
    other methods' reported weight is the submission, so it is matched as before, with its
    evidence type as the method. Both stores are searched, evidence first: a set recorded as
    evidence before P4-3 stays that record, returned with its stored value and never
    reclassified.
    """
    row = BenchmarkObservation
    is_set = report.method == "rep_set"
    conditions = [
        row.user_id == user_id,
        BenchmarkDefinition.code == report.benchmark_code,
        row.provenance_operation == oa.OP_STRENGTH_REPORT,
        row.collection_mode == collection_mode,
        row.performed_at.is_not_distinct_from(performed_at),
        row.load_kg.is_not_distinct_from(report.load_kg),
        row.reps.is_not_distinct_from(report.reps),
        row.rpe.is_not_distinct_from(report.rpe),
        row.rir.is_not_distinct_from(report.rir),
    ]
    if not is_set:
        conditions += [
            row.evidence_type == _observation_for(report, collection_mode).evidence_type,
            row.raw_value == report.value_kg,
        ]
    obs = (await db.execute(
        select(row)
        .join(BenchmarkDefinition, row.benchmark_definition_id == BenchmarkDefinition.id)
        .where(*conditions)
        .order_by(row.id)
        .limit(1)
    )).scalar_one_or_none()
    if obs is not None:
        # The existing record, as it is: a set recorded as evidence before P4-3 carries its
        # stored estimate, whatever its effort. Whether the submission seeds is decided apart,
        # from the submission's own classification (seed_values).
        return StrengthOutcome(
            outcome="already_recorded",
            value_available=True,
            observation=benchmark_service.observation_read(obs, report.benchmark_code),
        )
    if not is_set:
        return None
    rep = UnderivedStrengthReport
    found = (await db.execute(
        select(rep)
        .join(BenchmarkDefinition, rep.benchmark_definition_id == BenchmarkDefinition.id)
        .where(
            rep.user_id == user_id,
            BenchmarkDefinition.code == report.benchmark_code,
            rep.collection_mode == collection_mode,
            rep.performed_at.is_not_distinct_from(performed_at),
            rep.load_kg == report.load_kg,
            rep.reps == report.reps,
            rep.rpe.is_not_distinct_from(report.rpe),
            rep.rir.is_not_distinct_from(report.rir),
        )
        .order_by(rep.id)
        .limit(1)
    )).scalar_one_or_none()
    if found is None:
        return None
    report_read = _report_read(found, report.benchmark_code)
    return StrengthOutcome(
        outcome="already_recorded",
        reason=report_read.reason,
        value_available=False,
        report=report_read,
    )


async def _stage_projection(db: AsyncSession, user_id: int, code: str) -> None:
    """Derive the profile's value for one lift from the athlete's strength reports, in the
    caller's transaction.

    Reads only rows this module wrote (the server-set ``provenance_operation``). The value of
    the report performed most recently — undated reports rank below every dated one — then the
    newest report. The profile column is written here and nowhere else.

    A set recorded before P4-3 whose effort is unknown or conflicting (classified from its stored
    RPE and RIR by the shared resolver, here and not in SQL) is skipped: it would not be recorded
    as evidence today, so it does not decide the profile value. Its stored value is untouched and
    still read by KPIs and estimated-PR tracking. This runs only when new evidence is recorded, so
    a profile value derived before P4-3 changes only on the next characterized report.
    """
    rows = (await db.execute(
        select(BenchmarkObservation)
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
    )).scalars()
    value = next((obs.raw_value for obs in rows if observation_underived_reason(obs) is None), None)
    if value is None:
        return
    profile = await AthleteProfileRepository(db).get_for_user(user_id)
    if profile is None:
        profile = AthleteProfile(user_id=user_id)
        db.add(profile)
    setattr(profile, CANONICAL_LIFT_PROFILE_COLUMNS[code], float(value))
