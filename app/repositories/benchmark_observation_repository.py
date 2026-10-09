"""The one place that answers "which observation may size this athlete's load, as of when?".

Two readers resolve an athlete's current e1RM, and ADR-0056 requires them to agree on the
number: ``prescription_service._current_e1rm_values`` (what load to prescribe) and
``state_service.prelog_e1rm_denominators`` (the ``I = load / e1RM_pre`` denominator for
dose intensity). Both call :func:`select_prescription_basis`, which loads the candidate
observations and applies the pure rule in ``app.logic.prescription_evidence``. They agree
only for identical evidence and an identical ``as_of``: a prescription and a later or
backdated workout log can straddle an expiry, which is why ``as_of`` is required rather
than defaulted here.

History worth keeping. ``affects_prescription`` was once written by three paths and read by
none, so a row explicitly marked unfit to prescribe from still sized the bar; it is read as
an explicit permission, and NULL is refused. Until S2 the rule was a SQL predicate
(``validity_status == 'valid' AND affects_prescription IS TRUE``) with the latest
``observed_at`` winning. S2 moved eligibility, performance-time freshness, and selection
into the pure module, so the reason nothing qualified can be reported and so extraction's
``is_pr`` no longer doubles as prescription permission.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic.prescription_evidence import BasisSelection, EvidenceRow, select_basis
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation


def _usable_row() -> ColumnElement[bool]:
    return and_(
        # Positively valid: an unknown or pending status never qualifies (ingestion requires
        # "valid", and a pending row has received no state application).
        BenchmarkObservation.validity_status == "valid",
        BenchmarkObservation.quarantined_at.is_(None),
    )


def _current_provenance() -> ColumnElement[bool]:
    return and_(
        BenchmarkObservation.source_type.in_(oa.DEMONSTRATED_SOURCE_TYPES),
        BenchmarkObservation.evidence_type.in_(oa.DEMONSTRATED_EVIDENCE_TYPES),
        BenchmarkObservation.value_semantics == oa.se.VS_MEASURED,
        _not_protocol_invalid(),
    )


def _not_protocol_invalid() -> ColumnElement[bool]:
    return or_(
        BenchmarkObservation.protocol_validity.is_(None),
        BenchmarkObservation.protocol_validity != oa.PV_INVALID,
    )


def _migrated_legacy() -> ColumnElement[bool]:
    return and_(
        _not_protocol_invalid(),
        BenchmarkObservation.source_type == oa.ST_LEGACY_UNKNOWN,
        BenchmarkObservation.provenance_operation == oa.OP_SCHEMA_BACKFILL,
        BenchmarkObservation.migration_version == oa.LEGACY_MIGRATION_VERSION,
        BenchmarkObservation.authority_resolution_reason == oa.LEGACY_MIGRATION_REASON,
        BenchmarkObservation.observation_model == oa.LEGACY_OBSERVATION_MODEL,
        BenchmarkObservation.evidence_type == oa.se.EV_DIRECT_MEASUREMENT,
        BenchmarkObservation.value_semantics == oa.se.VS_MEASURED,
    )


def demonstrated_strength_clause() -> ColumnElement[bool]:
    """SQL form of :func:`app.logic.observation_authority.is_demonstrated_strength`, plus the
    query-level conditions (valid, not quarantined). A test pins the two forms to each other."""
    return and_(_usable_row(), _current_provenance())


def decline_protection_clause() -> ColumnElement[bool]:
    """SQL form of :func:`app.logic.observation_authority.is_decline_protection_evidence`:
    demonstrated strength, or a row carrying the legacy migration's whole record."""
    return and_(_usable_row(), or_(_current_provenance(), _migrated_legacy()))


async def _max_raw(
    db: AsyncSession, user_id: int, code: str, clause: ColumnElement[bool],
    *, exclude_observation_id: int | None, as_of: datetime | None,
) -> float | None:
    conditions = [BenchmarkObservation.user_id == user_id, BenchmarkDefinition.code == code, clause]
    if exclude_observation_id is not None:
        conditions.append(BenchmarkObservation.id != exclude_observation_id)
    if as_of is not None:
        conditions.append(BenchmarkObservation.observed_at <= as_of)
    res = await db.execute(
        select(func.max(BenchmarkObservation.raw_value))
        .join(BenchmarkDefinition, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(*conditions)
    )
    return res.scalar_one_or_none()


async def demonstrated_watermark(
    db: AsyncSession, user_id: int, code: str, *, exclude_observation_id: int | None = None
) -> float | None:
    """The best currently valid DEMONSTRATED e1RM for this athlete and lift (ADR-0066).

    The public "best validated" figure. Only measured max tests by current provenance count (see
    ``oa.is_demonstrated_strength``); migrated legacy history never does, because the migration
    cannot prove a max. Derived from ``max(raw_value)``, so it is monotone while valid tests are
    added and may fall when the top one is corrected or quarantined (a data correction, not a
    decline).
    """
    return await _max_raw(
        db, user_id, code, demonstrated_strength_clause(),
        exclude_observation_id=exclude_observation_id, as_of=None,
    )


async def decline_prior_watermark(
    db: AsyncSession, user_id: int, code: str, *,
    exclude_observation_id: int | None = None, as_of: datetime | None = None,
) -> float | None:
    """The prior a strength decline is judged against (ADR-0066): demonstrated strength plus the
    legacy migration's tests, so an older athlete's next low test is still a candidate. A training
    estimate is never in it, however high. ``as_of`` limits it to tests dated at or before that
    moment: the prior a candidate was opened against."""
    return await _max_raw(
        db, user_id, code, decline_protection_clause(),
        exclude_observation_id=exclude_observation_id, as_of=as_of,
    )


async def estimated_pr_baseline(
    db: AsyncSession, user_id: int, code: str, *, formula: str
) -> float | None:
    """The bar a training-derived e1RM must clear to be a PR (extraction's ``is_pr``).

    The best of the athlete's demonstrated strength and their earlier estimates made by the
    SAME formula. Estimates from a different formula are not comparable (a formula change is not
    progress), and an estimate is never a demonstrated watermark: this is PR tracking for
    estimates only, kept apart from :func:`demonstrated_watermark`.
    """
    res = await db.execute(
        select(func.max(BenchmarkObservation.raw_value))
        .join(BenchmarkDefinition, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == code,
            or_(
                demonstrated_strength_clause(),
                and_(
                    BenchmarkObservation.validity_status == "valid",
                    BenchmarkObservation.quarantined_at.is_(None),
                    BenchmarkObservation.formula == formula,
                ),
            ),
        )
    )
    return res.scalar_one_or_none()


async def select_prescription_basis(
    db: AsyncSession, user_id: int, codes: set[str], *, as_of: datetime
) -> dict[str, BasisSelection]:
    """Per benchmark code: the observation that may size a load at ``as_of``, or why none may.

    Every requested code is present in the result; a code with no observations at all
    carries ``REASON_NO_EVIDENCE``.
    """
    if not codes:
        return {}
    res = await db.execute(
        select(BenchmarkDefinition.code, BenchmarkObservation)
        .join(
            BenchmarkObservation,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code.in_(codes),
        )
    )
    rows_by_code: dict[str, list[EvidenceRow]] = defaultdict(list)
    for code, obs in res.all():
        rows_by_code[code].append(_evidence_row(obs))
    return {code: select_basis(rows_by_code.get(code, []), as_of=as_of) for code in codes}


def _evidence_row(obs: BenchmarkObservation) -> EvidenceRow:
    return EvidenceRow(
        observation_id=obs.id,
        raw_value=obs.raw_value,
        performed_at=obs.performed_at,
        validity_status=obs.validity_status,
        quarantined_at=obs.quarantined_at,
        affects_prescription=obs.affects_prescription,
        source_type=obs.source_type,
        value_semantics=obs.value_semantics,
        evidence_type=obs.evidence_type,
        source=obs.source,
        reps=obs.reps,
        load_kg=obs.load_kg,
        rpe=obs.rpe,
        rir=obs.rir,
        effort_fidelity=obs.effort_fidelity,
    )
