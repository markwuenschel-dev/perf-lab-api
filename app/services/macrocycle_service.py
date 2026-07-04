"""Macrocycle service — CRUD + the cross-block "week X of Y" computation
(Phase 5 of the goal-anchored program plan).

The schedule math is a pure helper (``compute_week_progress``) so it is
unit-testable without a DB session — see tests/test_macrocycle_progress.py.
Per ADR-0040 the horizon is *computed* from the macrocycle's ``start_date`` and
the anchor Objective's ``target_date``; nothing about future weeks is persisted.
"""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.macrocycle import Macrocycle, MacrocycleStatus
from app.models.mesocycle import MesocycleBlock
from app.models.objective import Objective
from app.schemas.macrocycle import MacrocycleCreate, MacrocycleRead, MacrocycleUpdate, WeekProgress

# ---------------------------------------------------------------------------
# Pure schedule math (non-DB, unit-testable)
# ---------------------------------------------------------------------------

def compute_week_progress(
    start_date: date,
    target_date: date | None,
    today: date | None = None,
) -> WeekProgress:
    """Cross-block "week X of Y" from the program start and the anchor target.

    ``current_week`` is ``floor(days_elapsed / 7) + 1``, clamped to ``>= 1``
    (a not-yet-started program reads as week 1) and capped at ``total_weeks``
    when the horizon is known. ``total_weeks`` is ``ceil(span_days / 7)`` and
    is ``None`` (open horizon) when there is no target or the target is on/before
    the start. ``pct`` is schedule position (elapsed/total), not benchmark
    progress. ``weeks_to_go`` never goes negative.
    """
    today = today or date.today()

    current_week = (today - start_date).days // 7 + 1
    if current_week < 1:
        current_week = 1

    if target_date is None or target_date <= start_date:
        return WeekProgress(current_week=current_week, total_weeks=None, pct=None, weeks_to_go=None)

    total_weeks = max(1, math.ceil((target_date - start_date).days / 7))
    if current_week > total_weeks:
        current_week = total_weeks
    pct = max(0.0, min(100.0, current_week / total_weeks * 100.0))
    weeks_to_go = max(0, math.ceil((target_date - today).days / 7))
    return WeekProgress(
        current_week=current_week, total_weeks=total_weeks, pct=pct, weeks_to_go=weeks_to_go
    )


# ---------------------------------------------------------------------------
# Read assembly (DB-touching: resolves the anchor objective + block count)
# ---------------------------------------------------------------------------

async def to_read_schema(db: AsyncSession, macrocycle: Macrocycle) -> MacrocycleRead:
    """Assemble the API-facing ``MacrocycleRead`` — the anchor objective's
    label/target_date (denormalized for display) plus the computed
    ``week_progress`` and the count of blocks hanging under this macrocycle."""
    objective = (
        await db.execute(select(Objective).where(Objective.id == macrocycle.objective_id))
    ).scalars().first()
    objective_label = objective.label if objective is not None else ""
    target_date = objective.target_date if objective is not None else None

    block_count = (
        await db.execute(
            select(func.count())
            .select_from(MesocycleBlock)
            .where(MesocycleBlock.macrocycle_id == macrocycle.id)
        )
    ).scalar_one()

    return MacrocycleRead(
        id=macrocycle.id,
        user_id=macrocycle.user_id,
        objective_id=macrocycle.objective_id,
        start_date=macrocycle.start_date,
        status=macrocycle.status,
        created_at=macrocycle.created_at,
        updated_at=macrocycle.updated_at,
        objective_label=objective_label,
        target_date=target_date,
        block_count=block_count,
        week_progress=compute_week_progress(macrocycle.start_date, target_date),
    )


# ---------------------------------------------------------------------------
# CRUD (all user-scoped — no IDOR)
# ---------------------------------------------------------------------------

async def _get_owned_objective(
    db: AsyncSession, user_id: int, objective_id: int
) -> Objective | None:
    """The objective iff it belongs to ``user_id`` — the anchor-ownership gate
    that keeps a macrocycle from pointing at another user's objective."""
    return (
        await db.execute(
            select(Objective).where(Objective.id == objective_id, Objective.user_id == user_id)
        )
    ).scalars().first()


async def create_macrocycle(
    db: AsyncSession, user_id: int, payload: MacrocycleCreate
) -> Macrocycle | None:
    """Create a macrocycle anchored to one of the caller's objectives. Returns
    ``None`` when ``objective_id`` is unknown or not owned (router → 400)."""
    if await _get_owned_objective(db, user_id, payload.objective_id) is None:
        return None

    macrocycle = Macrocycle(
        user_id=user_id,
        objective_id=payload.objective_id,
        start_date=payload.start_date or date.today(),
    )
    db.add(macrocycle)
    await db.commit()
    await db.refresh(macrocycle)
    return macrocycle


async def list_macrocycles(
    db: AsyncSession, user_id: int, status_filter: MacrocycleStatus | None = None
) -> list[Macrocycle]:
    """Active macrocycles by default; pass ``status_filter`` to see others."""
    stmt = (
        select(Macrocycle)
        .where(Macrocycle.user_id == user_id)
        .where(Macrocycle.status == (status_filter or MacrocycleStatus.ACTIVE))
        .order_by(Macrocycle.start_date.asc(), Macrocycle.id.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_macrocycle(db: AsyncSession, user_id: int, macrocycle_id: int) -> Macrocycle | None:
    result = await db.execute(
        select(Macrocycle).where(Macrocycle.id == macrocycle_id, Macrocycle.user_id == user_id)
    )
    return result.scalars().first()


async def update_macrocycle(
    db: AsyncSession, user_id: int, macrocycle_id: int, payload: MacrocycleUpdate
) -> Macrocycle | None:
    macrocycle = await get_macrocycle(db, user_id, macrocycle_id)
    if macrocycle is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(macrocycle, field, value)
    await db.commit()
    await db.refresh(macrocycle)
    return macrocycle


async def delete_macrocycle(db: AsyncSession, user_id: int, macrocycle_id: int) -> bool:
    macrocycle = await get_macrocycle(db, user_id, macrocycle_id)
    if macrocycle is None:
        return False
    await db.delete(macrocycle)
    await db.commit()
    return True
