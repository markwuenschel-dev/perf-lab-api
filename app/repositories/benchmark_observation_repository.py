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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.prescription_evidence import BasisSelection, EvidenceRow, select_basis
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation


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
