"""Objective service — CRUD, direction-aware progress, and the prescriber
signal helper (Phase 4a of the goal-anchored program plan).

Progress math is split into a pure helper (``compute_progress_pct``) so it is
unit-testable without a DB session — see tests/test_objective_progress.py.
"""
from __future__ import annotations

from datetime import date
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.objective import Objective, ObjectiveStatus
from app.schemas.objective import ObjectiveCreate, ObjectiveRead, ObjectiveUpdate, ProgressBlock

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
    (no ``benchmark_code``) or when no observation / definition is found."""
    if objective.benchmark_code is None:
        return ProgressBlock(current=None, target=objective.target_value, pct=None, direction=None)

    definition_result = await db.execute(
        select(BenchmarkDefinition).where(BenchmarkDefinition.code == objective.benchmark_code)
    )
    definition = definition_result.scalars().first()
    if definition is None:
        return ProgressBlock(current=None, target=objective.target_value, pct=None, direction=None)

    obs_result = await db.execute(
        select(BenchmarkObservation)
        .where(
            BenchmarkObservation.user_id == objective.user_id,
            BenchmarkObservation.benchmark_definition_id == definition.id,
        )
        .order_by(BenchmarkObservation.observed_at.desc())
        .limit(1)
    )
    latest = obs_result.scalars().first()
    current = latest.raw_value if latest is not None else None
    pct = compute_progress_pct(current, objective.target_value, definition.better_direction)
    return ProgressBlock(
        current=current,
        target=objective.target_value,
        pct=pct,
        direction=definition.better_direction,
    )


async def to_read_schema(db: AsyncSession, objective: Objective) -> ObjectiveRead:
    """Assemble the full API-facing ``ObjectiveRead``, including the
    computed ``progress`` block and ``days_to_go`` countdown."""
    progress = await compute_progress(db, objective)
    return ObjectiveRead(
        id=objective.id,
        user_id=objective.user_id,
        benchmark_code=objective.benchmark_code,
        label=objective.label,
        domain=objective.domain,
        target_value=objective.target_value,
        target_unit=objective.target_unit,
        target_date=objective.target_date,
        priority=objective.priority,
        status=objective.status,
        created_at=objective.created_at,
        progress=progress,
        days_to_go=days_to_go(objective.target_date),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_objective(db: AsyncSession, user_id: int, payload: ObjectiveCreate) -> Objective:
    domain = payload.domain
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
# ---------------------------------------------------------------------------

async def active_objective_signals(db: AsyncSession, user_id: int) -> ObjectiveSignals:
    """``{ taper, domain }`` for the prescriber (both entry points — see
    app.services.prescription_service and app.api.v1.planning's ``/today``).

    - ``taper``: True when the nearest *upcoming* active objective's
      ``target_date`` falls within ``OBJECTIVE_TAPER_WINDOW_DAYS`` days.
    - ``domain``: the highest-priority active objective's ``domain``
      (priority 1 = highest; ties broken by lowest ``id``, i.e. earliest
      created).
    """
    result = await db.execute(
        select(Objective).where(
            Objective.user_id == user_id,
            Objective.status == ObjectiveStatus.ACTIVE,
        )
    )
    objectives = list(result.scalars().all())
    if not objectives:
        return ObjectiveSignals(taper=False, domain=None)

    today = date.today()
    upcoming = [o for o in objectives if o.target_date is not None and o.target_date >= today]
    taper = False
    if upcoming:
        nearest = min(upcoming, key=lambda o: o.target_date)  # type: ignore[arg-type,return-value]
        assert nearest.target_date is not None
        taper = (nearest.target_date - today).days <= OBJECTIVE_TAPER_WINDOW_DAYS

    top = min(objectives, key=lambda o: (o.priority, o.id))
    return ObjectiveSignals(taper=taper, domain=top.domain)
