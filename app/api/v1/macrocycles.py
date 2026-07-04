"""Macrocycles — the thin program container above blocks (Phase 5).

``POST   /v1/macrocycles``       create (anchored to one of the caller's objectives)
``GET    /v1/macrocycles``       list (active by default; ``?status=`` filter)
``GET    /v1/macrocycles/{id}``  read one (with computed "week X of Y")
``PATCH  /v1/macrocycles/{id}``  partial update (start_date, status)
``DELETE /v1/macrocycles/{id}``  delete (detaches its blocks, never deletes them)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.macrocycle import MacrocycleStatus
from app.models.user import User
from app.schemas.macrocycle import MacrocycleCreate, MacrocycleRead, MacrocycleUpdate
from app.services import macrocycle_service

router = APIRouter(prefix="/macrocycles", tags=["Macrocycles"])


@router.post("", response_model=MacrocycleRead)
async def create_macrocycle(
    payload: MacrocycleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MacrocycleRead:
    macrocycle = await macrocycle_service.create_macrocycle(db, current_user.id, payload)
    if macrocycle is None:
        raise HTTPException(status_code=400, detail="objective_id not found or not owned")
    return await macrocycle_service.to_read_schema(db, macrocycle)


@router.get("", response_model=list[MacrocycleRead])
async def list_macrocycles(
    status: MacrocycleStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MacrocycleRead]:
    macrocycles = await macrocycle_service.list_macrocycles(db, current_user.id, status_filter=status)
    return [await macrocycle_service.to_read_schema(db, m) for m in macrocycles]


@router.get("/{macrocycle_id}", response_model=MacrocycleRead)
async def get_macrocycle(
    macrocycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MacrocycleRead:
    macrocycle = await macrocycle_service.get_macrocycle(db, current_user.id, macrocycle_id)
    if macrocycle is None:
        raise HTTPException(status_code=404, detail="Macrocycle not found")
    return await macrocycle_service.to_read_schema(db, macrocycle)


@router.patch("/{macrocycle_id}", response_model=MacrocycleRead)
async def update_macrocycle(
    macrocycle_id: int,
    payload: MacrocycleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MacrocycleRead:
    macrocycle = await macrocycle_service.update_macrocycle(
        db, current_user.id, macrocycle_id, payload
    )
    if macrocycle is None:
        raise HTTPException(status_code=404, detail="Macrocycle not found")
    return await macrocycle_service.to_read_schema(db, macrocycle)


@router.delete("/{macrocycle_id}", status_code=204, response_model=None)
async def delete_macrocycle(
    macrocycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = await macrocycle_service.delete_macrocycle(db, current_user.id, macrocycle_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Macrocycle not found")
