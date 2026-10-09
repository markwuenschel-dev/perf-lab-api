"""What turning on chart e1RM estimates would do to each athlete's prescription basis (P4-2b).

Read-only. The chart estimate is 0-12% above Epley for the same set, and the prescription selector
takes the highest eligible value, so one new chart estimate moves an athlete's basis by the whole
step on their next qualifying set. The basis is also the pre-log e1RM the dose ladder divides by
(``I = load / e1rm_pre``): a higher basis means a lower relative load, and so a smaller external
dose for the same set. Freezing the v0 dose operator does not freeze that denominator. This
reports both, per athlete and lift, before ``E1RM_CHART_ESTIMATES`` is switched on.

For every set-derived row still in the freshness window that was estimated with Epley and has the
facts to be re-estimated (load, reps, a known and consistent effort), the report restates its
value with the chart and re-selects the basis exactly as the prescription does.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.logic.prescription_evidence import EvidenceRow, select_basis
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.repositories.benchmark_observation_repository import evidence_row
from app.services.e1rm_estimation import estimate_set_e1rm
from app.services.strength_evidence_service import canonical_e1rm_codes


@dataclass(frozen=True)
class ActivationLine:
    user_id: int
    code: str
    basis_now_kg: float | None  # what sizes a load today
    basis_chart_kg: float | None  # what would, with the chart
    restated_rows: int  # eligible Epley rows the chart would re-estimate

    @property
    def step_pct(self) -> float | None:
        if self.basis_now_kg is None or self.basis_chart_kg is None or self.basis_now_kg == 0:
            return None
        return (self.basis_chart_kg / self.basis_now_kg - 1.0) * 100.0

    @property
    def relative_load_factor(self) -> float | None:
        """The factor ``load / e1rm_pre`` is multiplied by (the dose ladder's top rung)."""
        if self.basis_now_kg is None or self.basis_chart_kg in (None, 0):
            return None
        assert self.basis_chart_kg is not None
        return self.basis_now_kg / self.basis_chart_kg


def _restated(row: EvidenceRow, formula: str | None) -> EvidenceRow | None:
    """The row with its value re-estimated by the chart, or None if the chart would not change it."""
    if formula not in (None, sc.FORMULA_EPLEY):
        return None  # already a chart estimate (or another model): nothing to restate
    if row.load_kg is None or row.reps is None:
        return None
    estimate = estimate_set_e1rm(
        row.load_kg, row.reps, row.rpe, row.rir,
        row.effort_fidelity or se.FIDELITY_UNSTATED, chart_enabled=True,
    )
    if not estimate.modeled:
        return None
    return replace(row, raw_value=estimate.value)


async def activation_report(
    db: AsyncSession, *, as_of: datetime, user_id: int | None = None
) -> list[ActivationLine]:
    codes = await canonical_e1rm_codes(db)
    if not codes:
        return []
    query = (
        select(BenchmarkDefinition.code, BenchmarkObservation)
        .join(BenchmarkObservation, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(BenchmarkDefinition.code.in_(codes))
    )
    if user_id is not None:
        query = query.where(BenchmarkObservation.user_id == user_id)
    grouped: dict[tuple[int, str], list[tuple[EvidenceRow, str | None]]] = defaultdict(list)
    for code, obs in (await db.execute(query)).all():
        grouped[(obs.user_id, code)].append((evidence_row(obs), obs.formula))

    lines: list[ActivationLine] = []
    for (uid, code), rows in sorted(grouped.items()):
        now_sel = select_basis([r for r, _ in rows], as_of=as_of).selected
        restated_rows = 0
        chart_rows: list[EvidenceRow] = []
        for row, formula in rows:
            alt = _restated(row, formula)
            if alt is not None:
                restated_rows += 1
            chart_rows.append(alt if alt is not None else row)
        chart_sel = select_basis(chart_rows, as_of=as_of).selected
        if now_sel is None and chart_sel is None:
            continue
        lines.append(ActivationLine(
            user_id=uid, code=code,
            basis_now_kg=now_sel.raw_value if now_sel else None,
            basis_chart_kg=chart_sel.raw_value if chart_sel else None,
            restated_rows=restated_rows,
        ))
    return lines
