"""A counterfactual for chart e1RM estimates: the in-window history, re-estimated (P4-2b).

Read-only. It answers one question: *if the sets that currently size an athlete's load had been
estimated with the chart, what would the basis be?* It does not predict what happens when the
flag is switched on. History stays Epley, so a future set competes with the existing rows by
value: a new chart estimate raises the basis only if it is the highest eligible value, and by
at most the gap to the best existing row. How big a jump the next set causes depends on that set.

A row is restated only if all of these hold, each checked positively:

* it is Epley-derived: ``formula == 'epley'`` with a training-set/lower-bound label (a row
  with no recorded formula, or a measurement, is never restated, whatever fields it carries);
* it could size a load today (``ineligibility`` is None: valid, permitted, characterized,
  qualifying set, dated, not future, inside the freshness window);
* the chart would speak for it (a known, consistent effort that clears the extraction gate).

The basis is also the pre-log e1RM the dose ladder divides by (``I = load / e1rm_pre``): a
higher basis means a lower relative load for the same set. Freezing the v0 dose operator does not
freeze that denominator, so the report prints that factor too.

A second, separate counterfactual covers Relative Total (:func:`relative_total_report`). Projected
Total reads the LATEST valid row of each lift, whatever its age or eligibility, and Relative Total
gates the two SBD template variants at 3.0. So it re-estimates those latest rows and says whether
that would put an athlete on the other side of 3.0.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se
from app.logic.candidate_library import pl_total_below_3x  # the gate itself: one number, one place
from app.logic.prescription_evidence import EvidenceRow, ineligibility, select_basis
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.derived_metric_definition import DerivedMetricDefinition
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.repositories.benchmark_observation_repository import evidence_row
from app.services.e1rm_estimation import estimate_set_e1rm
from app.services.strength_evidence_service import canonical_e1rm_codes


@dataclass(frozen=True)
class ActivationLine:
    user_id: int
    code: str
    basis_now_kg: float | None  # what sizes a load today
    basis_chart_kg: float | None  # what would, with the chart
    restated_rows: int  # eligible, Epley-derived rows the chart would re-estimate

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


_EPLEY_EVIDENCE = frozenset({se.EV_LOWER_BOUND, se.EV_ESTIMATED_FROM_TRAINING_SET})
_EPLEY_SEMANTICS = frozenset({se.VS_LOWER_BOUND, se.VS_ESTIMATED})


def _epley_derived(obs: BenchmarkObservation) -> bool:
    """Positive provenance: this row's value was made by Epley from its set."""
    return (
        obs.formula == sc.FORMULA_EPLEY
        and obs.evidence_type in _EPLEY_EVIDENCE
        and obs.value_semantics in _EPLEY_SEMANTICS
    )


def _restated(
    row: EvidenceRow, obs: BenchmarkObservation, *, as_of: datetime
) -> EvidenceRow | None:
    """The row with its value re-estimated by the chart, or None if it would not be restated.
    Only a row that could size a load today (the basis counterfactual)."""
    if ineligibility(row, as_of=as_of) is not None:
        return None
    return _restated_any(row, obs)


def _restated_any(row: EvidenceRow, obs: BenchmarkObservation) -> EvidenceRow | None:
    """The same, without the prescription-eligibility condition (the KPI counterfactual)."""
    if not _epley_derived(obs):
        return None
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
    grouped: dict[tuple[int, str], list[tuple[EvidenceRow, BenchmarkObservation]]] = defaultdict(list)
    for code, obs in (await db.execute(query)).all():
        grouped[(obs.user_id, code)].append((evidence_row(obs), obs))

    lines: list[ActivationLine] = []
    for (uid, code), rows in sorted(grouped.items()):
        now_sel = select_basis([r for r, _ in rows], as_of=as_of).selected
        restated_rows = 0
        chart_rows: list[EvidenceRow] = []
        for row, obs in rows:
            alt = _restated(row, obs, as_of=as_of)
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


@dataclass(frozen=True)
class RelativeTotalLine:
    user_id: int
    total_now_kg: float
    total_chart_kg: float
    bodyweight_kg: float
    restated_lifts: int  # lifts whose latest valid row the chart would re-estimate

    @property
    def relative_now(self) -> float:
        return self.total_now_kg / self.bodyweight_kg

    @property
    def relative_chart(self) -> float:
        return self.total_chart_kg / self.bodyweight_kg

    @property
    def crosses_template_gate(self) -> bool:
        """Whether the restatement puts the athlete on the other side of the 3.0 gate, i.e. which
        of the two SBD template variants is eligible."""
        return pl_total_below_3x({"pl_relative_total": self.relative_now}) != pl_total_below_3x(
            {"pl_relative_total": self.relative_chart}
        )


async def relative_total_report(db: AsyncSession, *, user_id: int | None = None) -> list[RelativeTotalLine]:
    """Counterfactual: Relative Total today vs with each lift's latest valid row re-estimated.

    Mirrors the KPI's own reads: the latest valid row per lift (no age, eligibility or gate), the
    Projected Total's own benchmark codes, the profile's bodyweight. Athletes missing a lift or a
    bodyweight have no Relative Total and are not listed. Read-only.
    """
    definition = (await db.execute(
        select(DerivedMetricDefinition).where(DerivedMetricDefinition.code == "pl_projected_total")
    )).scalars().first()
    codes: list[str] = list((definition.formula_config or {}).get("benchmark_codes") or []) if definition else []
    if not codes:
        return []
    query = (
        select(BenchmarkDefinition.code, BenchmarkObservation)
        .join(BenchmarkObservation, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(BenchmarkDefinition.code.in_(codes), BenchmarkObservation.validity_status == "valid")
        .order_by(BenchmarkObservation.observed_at.desc())
    )
    if user_id is not None:
        query = query.where(BenchmarkObservation.user_id == user_id)
    latest: dict[int, dict[str, BenchmarkObservation]] = defaultdict(dict)
    for code, obs in (await db.execute(query)).all():
        latest[obs.user_id].setdefault(code, obs)

    lines: list[RelativeTotalLine] = []
    for uid, by_code in sorted(latest.items()):
        if set(by_code) != set(codes):
            continue  # Projected Total needs every lift
        profile = await AthleteProfileRepository(db).get_for_user(uid)
        bodyweight = float(profile.bodyweight_kg) if profile and profile.bodyweight_kg else 0.0
        if bodyweight <= 0:
            continue
        now_total = chart_total = 0.0
        restated = 0
        for code in codes:
            obs = by_code[code]
            now_total += obs.raw_value
            alt = _restated_any(evidence_row(obs), obs)
            if alt is not None:
                restated += 1
            chart_total += alt.raw_value if alt is not None and alt.raw_value is not None else obs.raw_value
        lines.append(RelativeTotalLine(
            user_id=uid, total_now_kg=now_total, total_chart_kg=chart_total,
            bodyweight_kg=bodyweight, restated_lifts=restated,
        ))
    return lines
