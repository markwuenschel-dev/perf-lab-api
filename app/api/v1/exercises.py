"""
app/api/v1/exercises.py

Read-only exercise catalog for the per-set log UI (ADR-0045). The web log surface
is catalog-bound — the exercise's ``load_type`` types which set fields are shown —
so it needs to list/search the movement library. Public: the catalog is seed data,
not user-specific.
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.exercise import Exercise

router = APIRouter(prefix="/exercises", tags=["Exercises"])


class ExerciseCatalogOut(BaseModel):
    """The fields the log UI needs to render a catalog-bound set entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    modality: str
    movement_pattern: str
    load_type: str
    equipment_required: list[str]
    weak_point_tags: list[str]
    sport_domains: list[str]
    is_benchmark: bool
    e1rm_benchmark_code: str | None


@router.get("", response_model=list[ExerciseCatalogOut])
async def list_exercises(
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, description="Case-insensitive name search."),
    load_type: str | None = Query(default=None, description="Filter by load_type."),
    modality: str | None = Query(default=None, description="Filter by modality."),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[Exercise]:
    """List the movement catalog, optionally filtered — powers the log's exercise picker."""
    stmt = select(Exercise)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Exercise.name.ilike(like)))
    if load_type:
        stmt = stmt.where(Exercise.load_type == load_type)
    if modality:
        stmt = stmt.where(Exercise.modality == modality)
    stmt = stmt.order_by(Exercise.name.asc()).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())
