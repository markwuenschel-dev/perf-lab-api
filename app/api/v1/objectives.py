"""Objectives — what an athlete trains toward (Phase 4a).

``POST   /v1/objectives``       create (benchmark-linked or free-text)
``GET    /v1/objectives``       list (active by default; ``?status=`` filter)
``PATCH  /v1/objectives/{id}``  partial update (incl. status)
``DELETE /v1/objectives/{id}``  delete
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.objective import ObjectiveStatus
from app.models.user import User
from app.schemas.objective import ObjectiveCreate, ObjectiveRead, ObjectiveUpdate
from app.services import objective_service

router = APIRouter(prefix="/objectives", tags=["Objectives"])


@router.post("", response_model=ObjectiveRead)
async def create_objective(
    payload: ObjectiveCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObjectiveRead:
    try:
        objective = await objective_service.create_objective(db, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await objective_service.to_read_schema(db, objective)


@router.get("", response_model=list[ObjectiveRead])
async def list_objectives(
    status: ObjectiveStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ObjectiveRead]:
    objectives = await objective_service.list_objectives(db, current_user.id, status_filter=status)
    return await objective_service.to_read_schemas(db, objectives)


@router.patch("/{objective_id}", response_model=ObjectiveRead)
async def update_objective(
    objective_id: int,
    payload: ObjectiveUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObjectiveRead:
    try:
        objective = await objective_service.update_objective(
            db, current_user.id, objective_id, payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if objective is None:
        raise HTTPException(status_code=404, detail="Objective not found")
    return await objective_service.to_read_schema(db, objective)


@router.delete("/{objective_id}", status_code=204, response_model=None)
async def delete_objective(
    objective_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = await objective_service.delete_objective(db, current_user.id, objective_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Objective not found")
