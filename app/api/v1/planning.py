from __future__ import annotations

from datetime import date
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.mesocycle import MesocycleBlock, PlannedSession
from app.models.user import User
from app.schemas.planning import (
    BlockCreateRequest,
    BlockRead,
    BlockUpdateRequest,
    PlannedSessionRead,
    PlannedSessionUpdateRequest,
    TodaySessionResponse,
    WeeklyTemplateSlot,
)
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.services import planning_service
from app.services.planning_service import create_block_with_sessions, get_today_session
from app.services.prescription_service import prescribe_for_athlete

router = APIRouter(prefix="/planning", tags=["Planning"])


@router.post("/blocks", response_model=BlockRead)
async def create_block(
    body: BlockCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MesocycleBlock:
    return await create_block_with_sessions(db, current_user.id, body)


@router.post("/blocks/preview", response_model=list[WeeklyTemplateSlot])
async def preview_block_template(
    body: BlockCreateRequest,
    current_user: User = Depends(get_current_user),
) -> list[WeeklyTemplateSlot]:
    """The week this request WOULD generate. Persists nothing.

    The create screen shows a style mix's real allocation with it, so "I asked for three
    styles and one got no sessions" is visible before the block exists rather than after.
    """
    return planning_service.preview_weekly_template(body)


@router.get("/blocks", response_model=list[BlockRead])
async def list_blocks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MesocycleBlock]:
    return await planning_service.list_blocks(db, current_user.id)


@router.patch("/blocks/{block_id}", response_model=BlockRead)
async def update_block(
    block_id: int,
    body: BlockUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MesocycleBlock:
    block = await planning_service.update_block(db, current_user.id, block_id, body)
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return block


@router.get("/sessions", response_model=list[PlannedSessionRead])
async def list_sessions(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlannedSession]:
    return await planning_service.list_sessions(db, current_user.id, start_date, end_date)


@router.patch("/sessions/{session_id}", response_model=PlannedSessionRead)
async def update_session(
    session_id: int,
    body: PlannedSessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlannedSession:
    session = await planning_service.update_session(db, current_user.id, session_id, body)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/today", response_model=TodaySessionResponse)
async def get_today(
    goal: str = Query(TRAINING_GOAL_DEFAULT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TodaySessionResponse:
    session = await get_today_session(db, current_user.id)
    if not session:
        return TodaySessionResponse(session=None, prescription=None)

    # Delegate to the single prescribe-and-persist seam so /planning/today and
    # /next-session agree by construction: same ADR-0030 goal resolution and the
    # same weak-point / KPI signals. Passing the session we resolved guarantees
    # the displayed session is the one the prescription was persisted into.
    # prescribe_for_athlete also handles state auto-init, objective signals, and
    # the decision-telemetry write, so this route no longer duplicates them.
    rx = await prescribe_for_athlete(
        db, current_user.id, cast(TrainingGoal, goal), planned_session=session
    )
    await db.refresh(session)
    return TodaySessionResponse(
        session=PlannedSessionRead.model_validate(session, from_attributes=True),
        # `prescription` is declared as WorkoutPrescription, so hand over the model
        # itself rather than flattening it first. While the field was `dict[str, Any]`,
        # `to_prescribed_content()` (== `model_dump()`) turned the model into an untyped
        # dict that the contract then published as a bare object — nothing validated it
        # on the way out, which is exactly what this change fixes. The serialized payload
        # is the same either way; `to_prescribed_content` keeps its one real job,
        # persisting into PlannedSession.prescribed_content, which prescribe_for_athlete
        # already did above.
        prescription=rx,
    )
