"""One benchmark assessment surface (ADR-0047).

Collapses onboarding-seed and the standalone Field Test into a single catalog view,
domain-filtered by the athlete's active domains, with a measurement-debt ranking of
which benchmarks to assess next. There are **no domain-specific seeders** — every
assessment is a ``benchmark_observation`` (submitted via ``benchmark_service`` through
the ADR-0058 authority path); domain-specificity lives in the definitions
(``domain_lenses``, ``state_targets``, protocol), never in separate screens/endpoints.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.logic import confidence_presentation as cp
from app.logic import domain_vocab as dv
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.objective import Objective, ObjectiveStatus
from app.models.user import AthleteProfile
from app.schemas.assessment import (
    AssessmentBenchmarkCard,
    AssessmentDomainGroup,
    AssessmentSurfaceRead,
)
from app.services import benchmark_service, state_service

UTILITY_MODEL_VERSION = "information_gain_proxy_v1"
SURFACE_POLICY_VERSION = "assessment_surface_v1"
MODE_ONRAMP = "onramp"
MODE_RETEST = "retest"
RECOMMEND_PER_DOMAIN = 3

# information_gain_proxy_v1 weights (see benchmark_utility).
_W_UNCERTAINTY = 1.0
_W_COVERAGE = 0.3
_W_BURDEN = 0.2
# Weak-prior uncertainty used when the twin has no live variance yet (fresh onramp).
_DEFAULT_UNCERTAINTY = 1.0


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def active_domain_lenses(
    objective_domains: list[str | None], primary_goal: str | None
) -> set[str]:
    """Canonical domains the athlete is training for — objectives + primary goal.

    Empty means "no declared domains": the surface then shows the whole catalog so an
    athlete can pick, never hiding the measurement layer behind an empty filter.
    """
    out: set[str] = set()
    for d in objective_domains:
        if d:
            c = dv.canonical_domain(d)
            if c in dv.DOMAINS:
                out.add(c)
    if primary_goal:
        g = dv.GOAL_TO_DOMAIN.get(primary_goal) or dv.canonical_domain(primary_goal)
        if g in dv.DOMAINS:
            out.add(g)
    return out


def benchmark_utility(
    measures_axes: list[str], variance_by_axis: dict[str, float] | None
) -> float:
    """information_gain_proxy_v1: expected uncertainty reduction from assessing this.

    ``utility = w_u·mean_uncertainty(measured axes) + w_c·coverage − w_b·burden``.
    A benchmark measuring high-variance axes (or, on a fresh onramp with no live
    variance, broad coverage) ranks higher. NOT empirical calibration — a proxy for
    ranking measurement debt.
    """
    if measures_axes and variance_by_axis:
        unc = sum(
            variance_by_axis.get(a, _DEFAULT_UNCERTAINTY) for a in measures_axes
        ) / len(measures_axes)
    else:
        unc = _DEFAULT_UNCERTAINTY
    coverage = min(len(measures_axes) / 3.0, 1.0) if measures_axes else 0.0
    burden = 1.0
    return round(_W_UNCERTAINTY * unc + _W_COVERAGE * coverage - _W_BURDEN * burden, 6)


def _confidence_status(
    measures_axes: list[str], variance_by_axis: dict[str, float] | None
) -> str | None:
    """Worst-axis certainty band (highest variance) over the measured axes."""
    if not measures_axes or not variance_by_axis:
        return None
    worst = max(variance_by_axis.get(a, _DEFAULT_UNCERTAINTY) for a in measures_axes)
    return cp.confidence_status(worst)


# --------------------------------------------------------------------------
# DB build
# --------------------------------------------------------------------------

async def _last_observed_by_code(db: AsyncSession, user_id: int) -> dict[str, datetime]:
    result = await db.execute(
        select(
            BenchmarkDefinition.code,
            func.max(BenchmarkObservation.observed_at),
        )
        .join(
            BenchmarkObservation,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(BenchmarkObservation.user_id == user_id)
        .group_by(BenchmarkDefinition.code)
    )
    return {code: ts for code, ts in result.all() if ts is not None}


async def build_assessment_surface(
    db: AsyncSession, user_id: int, mode: str
) -> AssessmentSurfaceRead:
    if mode not in (MODE_ONRAMP, MODE_RETEST):
        raise ValueError(f"mode must be onramp|retest, got {mode!r}")

    # Active domains from objectives + primary goal.
    obj_rows = await db.execute(
        select(Objective.domain).where(
            Objective.user_id == user_id, Objective.status == ObjectiveStatus.ACTIVE
        )
    )
    objective_domains = [d for (d,) in obj_rows.all()]
    prof_row = await db.execute(
        select(AthleteProfile.primary_goal).where(AthleteProfile.user_id == user_id)
    )
    primary_goal = prof_row.scalars().first()
    active = active_domain_lenses(objective_domains, primary_goal)
    show_all = not active

    # Live per-axis variance (the sole provisionality authority, ADR-0059).
    state = await state_service.load_current_state(db, user_id)
    variance_by_axis: dict[str, float] | None = (
        {axis: float(getattr(state.capacity_confidence, axis)) for axis in CapacityState.KEYS}
        if state is not None
        else None
    )
    last_obs = await _last_observed_by_code(db, user_id)

    definitions = await benchmark_service.list_definitions(db)
    grouped: dict[str, list[tuple[AssessmentBenchmarkCard, float]]] = {}
    for d in definitions:
        if d.is_derived_only:
            continue
        lenses, source = dv.resolve_domain_lenses(d.domain, d.domain_lenses)
        eligible = show_all or bool(set(lenses) & active)
        measures = list(d.state_targets or [])
        util = benchmark_utility(measures, variance_by_axis)
        card = AssessmentBenchmarkCard(
            code=d.code,
            name=d.name,
            domain=d.domain,
            domain_lenses=lenses,
            domain_lenses_source=source,
            metric_type=d.metric_type,
            unit=d.unit,
            protocol_summary=d.protocol_summary,
            measures_axes=measures,
            confidence_status=_confidence_status(measures, variance_by_axis),
            last_observed_at=last_obs.get(d.code),
            eligible=eligible,
            recommended=False,
            recommend_rank=None,
            utility=util,
            utility_model_version=UTILITY_MODEL_VERSION,
        )
        grouped.setdefault(d.domain, []).append((card, util))

    # Recommend the top few eligible benchmarks per domain by utility (measurement debt).
    recommended: list[tuple[str, float]] = []
    for cards in grouped.values():
        eligible_ranked = sorted(
            (c for c in cards if c[0].eligible), key=lambda c: c[1], reverse=True
        )
        for card, util in eligible_ranked[:RECOMMEND_PER_DOMAIN]:
            card.recommended = True
            recommended.append((card.code, util))

    recommended.sort(key=lambda cu: cu[1], reverse=True)
    for rank, (code, _u) in enumerate(recommended, start=1):
        for cards in grouped.values():
            for card, _ in cards:
                if card.code == code:
                    card.recommend_rank = rank

    # Assemble groups: only domains with ≥1 eligible card (or all when show_all).
    groups: list[AssessmentDomainGroup] = []
    for domain in sorted(grouped):
        cards = [c for c, _ in grouped[domain]]
        if show_all or any(c.eligible for c in cards):
            cards.sort(key=lambda c: (not c.eligible, c.recommend_rank or 1_000, c.name))
            groups.append(AssessmentDomainGroup(domain=domain, cards=cards))

    return AssessmentSurfaceRead(
        mode=mode,
        active_domains=sorted(active),
        groups=groups,
        recommended=[code for code, _ in recommended],
        policy_version=SURFACE_POLICY_VERSION,
    )
