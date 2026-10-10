"""Objective service — CRUD, direction-aware progress, and the prescriber
signal helper (Phase 4a of the goal-anchored program plan).

Progress math is split into a pure helper (``compute_progress_pct``) so it is
unit-testable without a DB session — see tests/test_objective_progress.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TypedDict, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.domain_vocab import normalize_domain_at_boundary
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.objective import Objective, ObjectiveStatus
from app.repositories.benchmark_observation_repository import not_modeled_estimate_clause
from app.schemas.objective import (
    DrivingObjectiveSource,
    ObjectiveCreate,
    ObjectiveRead,
    ObjectiveUpdate,
    ProgressBlock,
)
from app.services import macrocycle_service

# Prescriber taper window (Phase 4a). The nearest upcoming active objective's
# target_date within this many days triggers taper in the prescriber
# (app.logic.prescriber consumes the resulting `objective_taper` bool).
OBJECTIVE_TAPER_WINDOW_DAYS = 14


class ObjectiveSignals(TypedDict):
    taper: bool
    domain: str | None


# ---------------------------------------------------------------------------
# Pure progress math (non-DB, unit-testable)
# ---------------------------------------------------------------------------

def compute_progress_pct(
    current: float | None,
    target: float | None,
    better_direction: str | None,
) -> float | None:
    """Direction-aware progress percentage toward ``target``, clamped to
    [0, 100]. Ratio-based so no separate baseline value is needed:

    - ``better_direction == "lower"`` (e.g. a run time): ``target / current``.
      A current value already at or below target yields 100%.
    - ``better_direction == "higher"`` (e.g. a lift): ``current / target``.
      A current value at or above target yields 100%.

    Returns ``None`` when any input is missing, non-positive where a
    division would occur, or ``better_direction`` is unrecognized.
    """
    if current is None or target is None or better_direction is None:
        return None
    if better_direction == "lower":
        if current <= 0:
            return None
        pct = (target / current) * 100.0
    elif better_direction == "higher":
        if target <= 0:
            return None
        pct = (current / target) * 100.0
    else:
        return None
    return max(0.0, min(100.0, pct))


def days_to_go(target_date: date | None) -> int | None:
    if target_date is None:
        return None
    return (target_date - date.today()).days


# ---------------------------------------------------------------------------
# Progress (DB-touching: resolves the linked definition + latest observation)
# ---------------------------------------------------------------------------

async def compute_progress(db: AsyncSession, objective: Objective) -> ProgressBlock:
    """Progress for one objective. Null progress for free-text objectives
    (no ``benchmark_code``) or when no observation / definition is found.

    Delegates to :func:`_compute_progress_batch` so single- and list-progress share one
    code path."""
    return (await _compute_progress_batch(db, [objective]))[objective.id]


async def _compute_progress_batch(
    db: AsyncSession, objectives: list[Objective]
) -> dict[int, ProgressBlock]:
    """Progress for many objectives, keyed by objective id, in a bounded number of
    queries. Mirrors :func:`compute_progress` exactly — including every null-progress
    case (no ``benchmark_code``, unknown definition, no observation).

    The per-row path issued up to two queries per objective (linked definition + latest
    observation), so listing N cost up to 2N round-trips. This resolves all definitions
    in one ``IN`` query and all candidate observations in one query, then picks the
    latest per (user, definition) in memory — matching the per-row ``ORDER BY
    observed_at DESC LIMIT 1``."""
    progress: dict[int, ProgressBlock] = {}
    coded = [o for o in objectives if o.benchmark_code is not None]
    for objective in objectives:
        if objective.benchmark_code is None:
            progress[objective.id] = ProgressBlock(
                current=None, target=objective.target_value, pct=None, direction=None
            )
    if not coded:
        return progress

    codes = {o.benchmark_code for o in coded}
    definitions = (
        await db.execute(select(BenchmarkDefinition).where(BenchmarkDefinition.code.in_(codes)))
    ).scalars().all()
    definition_by_code = {d.code: d for d in definitions}
    definition_ids = {d.id for d in definitions}

    latest_obs: dict[tuple[int, int], BenchmarkObservation] = {}
    if definition_ids:
        user_ids = {o.user_id for o in coded}
        observations = (
            await db.execute(
                select(BenchmarkObservation)
                .where(
                    BenchmarkObservation.user_id.in_(user_ids),
                    BenchmarkObservation.benchmark_definition_id.in_(definition_ids),
                    # Attainment is demonstrated: a modeled estimate is never the "latest".
                    not_modeled_estimate_clause(),
                )
                .order_by(BenchmarkObservation.observed_at.desc())
            )
        ).scalars().all()
        for obs in observations:
            # Rows arrive newest-first, so the first seen per (user, definition) is the
            # latest — the same row the per-row LIMIT 1 would have returned.
            latest_obs.setdefault((obs.user_id, obs.benchmark_definition_id), obs)

    for objective in coded:
        code = objective.benchmark_code
        definition = definition_by_code.get(code) if code is not None else None
        if definition is None:
            progress[objective.id] = ProgressBlock(
                current=None, target=objective.target_value, pct=None, direction=None
            )
            continue
        latest = latest_obs.get((objective.user_id, definition.id))
        current = latest.raw_value if latest is not None else None
        pct = compute_progress_pct(current, objective.target_value, definition.better_direction)
        progress[objective.id] = ProgressBlock(
            current=current,
            target=objective.target_value,
            pct=pct,
            direction=definition.better_direction,
            current_evidence_type=latest.evidence_type if latest is not None else None,
            current_value_semantics=latest.value_semantics if latest is not None else None,
        )
    return progress


async def to_read_schema(db: AsyncSession, objective: Objective) -> ObjectiveRead:
    """Assemble the full API-facing ``ObjectiveRead`` for one objective.

    Delegates to :func:`to_read_schemas` so single- and list-assembly share one path."""
    return (await to_read_schemas(db, [objective]))[0]


async def to_read_schemas(db: AsyncSession, objectives: list[Objective]) -> list[ObjectiveRead]:
    """Assemble ``ObjectiveRead`` for many objectives in a bounded number of queries,
    including the computed ``progress`` block and ``days_to_go`` countdown for each."""
    progress_by_id = await _compute_progress_batch(db, objectives)
    return [
        ObjectiveRead(
            id=objective.id,
            user_id=objective.user_id,
            benchmark_code=objective.benchmark_code,
            label=objective.label,
            domain=objective.domain,
            target_value=objective.target_value,
            target_unit=objective.target_unit,
            target_date=objective.target_date,
            priority=objective.priority,
            display_rank=objective.display_rank,
            status=objective.status,
            created_at=objective.created_at,
            progress=progress_by_id[objective.id],
            days_to_go=days_to_go(objective.target_date),
        )
        for objective in objectives
    ]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_objective(db: AsyncSession, user_id: int, payload: ObjectiveCreate) -> Objective:
    # Operational DomainCode: canonicalize an inbound user value and reject a
    # non-canonical one (ADR-0057). A benchmark-derived domain is already
    # canonical after the seed correction, so we only normalize the user path.
    domain = normalize_domain_at_boundary(payload.domain)
    if payload.benchmark_code is not None and domain is None:
        definition_result = await db.execute(
            select(BenchmarkDefinition).where(BenchmarkDefinition.code == payload.benchmark_code)
        )
        definition = definition_result.scalars().first()
        if definition is not None:
            domain = definition.domain

    objective = Objective(
        user_id=user_id,
        benchmark_code=payload.benchmark_code,
        label=payload.label,
        domain=domain,
        target_value=payload.target_value,
        target_unit=payload.target_unit,
        target_date=payload.target_date,
        priority=payload.priority,
    )
    db.add(objective)
    await db.commit()
    await db.refresh(objective)
    return objective


async def list_objectives(
    db: AsyncSession, user_id: int, status_filter: ObjectiveStatus | None = None
) -> list[Objective]:
    """Active objectives by default; pass ``status_filter`` to see others."""
    stmt = select(Objective).where(Objective.user_id == user_id)
    stmt = stmt.where(Objective.status == (status_filter or ObjectiveStatus.ACTIVE))
    stmt = stmt.order_by(Objective.priority.asc(), Objective.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


def sort_for_display(objectives: Sequence[Objective]) -> list[Objective]:
    """The athlete's display order: ``display_rank`` ascending with never-ordered
    (NULL) objectives last, then ``priority``, then ``id`` — so a new objective lands at
    the bottom of an ordered list.

    Display only — not a weight (ADR-0061). ``list_objectives`` keeps its priority
    order and the prescriber's selector never reads ``display_rank``."""
    return sorted(
        objectives,
        key=lambda o: (o.display_rank is None, o.display_rank or 0, o.priority, o.id),
    )


class ObjectiveOrderError(ValueError):
    """The submitted order does not cover exactly the caller's active objectives."""


async def set_display_order(
    db: AsyncSession, user_id: int, objective_ids: list[int]
) -> list[Objective]:
    """Write ``display_rank`` 1..N for the caller's ACTIVE objectives, in one commit.

    ``objective_ids`` must name every active objective of ``user_id`` exactly once —
    a duplicate, a missing active objective, or an id that is not one of the caller's
    active objectives (another user's, or achieved/abandoned, or unknown) raises
    :class:`ObjectiveOrderError` and writes nothing. Display only — not a weight
    (ADR-0061): ``priority`` is never touched. Returns the objectives in the new order.
    """
    active = await list_objectives(db, user_id)
    active_by_id = {o.id: o for o in active}

    duplicates = sorted({i for i in objective_ids if objective_ids.count(i) > 1})
    submitted = set(objective_ids)
    missing = sorted(set(active_by_id) - submitted)
    unexpected = sorted(submitted - set(active_by_id))
    problems: list[str] = []
    if duplicates:
        problems.append(f"duplicate ids {duplicates}")
    if missing:
        problems.append(f"missing active objectives {missing}")
    if unexpected:
        problems.append(f"ids that are not your active objectives {unexpected}")
    if problems:
        raise ObjectiveOrderError(
            "objective_ids must list each of your active objectives exactly once: "
            + "; ".join(problems)
        )

    for rank, objective_id in enumerate(objective_ids, start=1):
        active_by_id[objective_id].display_rank = rank
    await db.commit()
    ordered = [active_by_id[i] for i in objective_ids]
    for objective in ordered:
        await db.refresh(objective)
    return ordered


async def get_objective(db: AsyncSession, user_id: int, objective_id: int) -> Objective | None:
    result = await db.execute(
        select(Objective).where(Objective.id == objective_id, Objective.user_id == user_id)
    )
    return result.scalars().first()


async def update_objective(
    db: AsyncSession, user_id: int, objective_id: int, payload: ObjectiveUpdate
) -> Objective | None:
    objective = await get_objective(db, user_id, objective_id)
    if objective is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "domain" in data:
        data["domain"] = normalize_domain_at_boundary(data["domain"])
    for field, value in data.items():
        setattr(objective, field, value)
    await db.commit()
    await db.refresh(objective)
    return objective


async def delete_objective(db: AsyncSession, user_id: int, objective_id: int) -> bool:
    objective = await get_objective(db, user_id, objective_id)
    if objective is None:
        return False
    await db.delete(objective)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Prescriber signal helper
#
# Signal derivation is split into two pure (non-DB) helpers so the taper-window
# and priority/nearest-date rules are unit-testable without a session — see
# tests/test_objective_signals.py. ``active_objective_signals`` only does the
# DB fetch + the "anchor vs. scan" routing.
# ---------------------------------------------------------------------------

def _target_within_taper_window(target_date: date | None, today: date) -> bool:
    """True when ``target_date`` is upcoming (today or later) and falls within
    ``OBJECTIVE_TAPER_WINDOW_DAYS`` days. A past or missing date never tapers."""
    if target_date is None or target_date < today:
        return False
    return (target_date - today).days <= OBJECTIVE_TAPER_WINDOW_DAYS


def signals_from_anchor(anchor: Objective, today: date | None = None) -> ObjectiveSignals:
    """Signals driven by a macrocycle's anchor objective (the program's goal):
    taper off the anchor's ``target_date`` within the window, domain = the
    anchor's ``domain``. Used when the user has an active macrocycle."""
    today = today or date.today()
    return ObjectiveSignals(
        taper=_target_within_taper_window(anchor.target_date, today),
        domain=anchor.domain,
    )


def signals_from_scan(objectives: list[Objective], today: date | None = None) -> ObjectiveSignals:
    """Legacy all-objectives scan (the pre-macrocycle behavior, preserved as the
    fallback): taper off the nearest *upcoming* objective's ``target_date``;
    domain = the highest-priority objective's ``domain`` (priority 1 = highest,
    ties broken by lowest ``id`` = earliest created)."""
    today = today or date.today()
    if not objectives:
        return ObjectiveSignals(taper=False, domain=None)

    upcoming = [o for o in objectives if o.target_date is not None and o.target_date >= today]
    taper = False
    if upcoming:
        nearest = min(upcoming, key=lambda o: cast("date", o.target_date))
        taper = _target_within_taper_window(nearest.target_date, today)

    top = top_priority_objective(objectives)
    return ObjectiveSignals(taper=taper, domain=top.domain if top is not None else None)


def top_priority_objective(objectives: Sequence[Objective]) -> Objective | None:
    """The scan path's driving objective: highest priority (1 = highest), ties broken by
    lowest ``id`` (earliest created). The athlete's display order plays no part (ADR-0061)."""
    if not objectives:
        return None
    return min(objectives, key=lambda o: (o.priority, o.id))


@dataclass(frozen=True)
class DrivingObjective:
    """What drives prescription: the chosen objective (None when nothing does), which
    lever chose it, and the signals derived from it — the same value the prescriber gets
    from :func:`active_objective_signals`."""

    objective: Objective | None
    source: DrivingObjectiveSource | None
    signals: ObjectiveSignals


async def active_objective_signals(db: AsyncSession, user_id: int) -> ObjectiveSignals:
    """``{ taper, domain }`` for the prescriber — the signals half of
    :func:`resolve_driving_objective`, so ``GET /v1/objectives/driving`` can never
    disagree with what prescription receives."""
    return (await resolve_driving_objective(db, user_id)).signals


async def resolve_driving_objective(db: AsyncSession, user_id: int) -> DrivingObjective:
    """The objective that drives prescription, the lever that chose it, and the
    ``{ taper, domain }`` signals for the prescriber (both entry points — see
    app.services.prescription_service and app.api.v1.planning's ``/today``). This is the
    one selector: ``GET /v1/objectives/driving`` reports its ``objective``/``source``.

    When the user has an active macrocycle, the signals derive from that
    program's *anchor objective* (the stored goal) — this is the Phase 5
    spine replacing the ad-hoc scan. ``list_macrocycles`` returns ACTIVE
    macrocycles ordered by ``start_date`` asc then ``id`` asc, so the first is
    the deterministic pick when several are active.

    With no active macrocycle (or a dangling anchor) we fall back to the legacy
    all-objectives scan, preserving the exact pre-macrocycle behavior.
    """
    macrocycles = await macrocycle_service.list_macrocycles(db, user_id)
    if macrocycles:
        anchor = (
            await db.execute(
                select(Objective).where(
                    Objective.id == macrocycles[0].objective_id,
                    Objective.user_id == user_id,
                )
            )
        ).scalars().first()
        if anchor is not None:
            return DrivingObjective(
                objective=anchor, source="macrocycle_anchor", signals=signals_from_anchor(anchor)
            )

    result = await db.execute(
        select(Objective).where(
            Objective.user_id == user_id,
            Objective.status == ObjectiveStatus.ACTIVE,
        )
    )
    objectives = list(result.scalars().all())
    top = top_priority_objective(objectives)
    return DrivingObjective(
        objective=top,
        source="priority" if top is not None else None,
        signals=signals_from_scan(objectives),
    )
