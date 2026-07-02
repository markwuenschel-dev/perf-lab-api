from __future__ import annotations

from datetime import date
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.mesocycle import MesocycleBlock, PlannedSession, SessionStatus
from app.models.user import User
from app.schemas.planning import (
    BlockCreateRequest,
    BlockRead,
    BlockUpdateRequest,
    PlannedSessionRead,
    PlannedSessionUpdateRequest,
    TodaySessionResponse,
)
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
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


@router.get("/blocks", response_model=list[BlockRead])
async def list_blocks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MesocycleBlock]:
    result = await db.execute(
        select(MesocycleBlock)
        .where(MesocycleBlock.user_id == current_user.id)
        .order_by(MesocycleBlock.created_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/blocks/{block_id}", response_model=BlockRead)
async def update_block(
    block_id: int,
    body: BlockUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MesocycleBlock:
    result = await db.execute(
        select(MesocycleBlock).where(
            and_(
                MesocycleBlock.id == block_id,
                MesocycleBlock.user_id == current_user.id,
            )
        )
    )
    block = result.scalars().first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    if body.status is not None:
        block.status = body.status
    if body.rationale is not None:
        block.rationale = body.rationale
    if body.modality_mix is not None:
        block.modality_mix = body.modality_mix
    if body.deload_volume_factor is not None:
        block.deload_volume_factor = body.deload_volume_factor
    await db.commit()
    await db.refresh(block)
    return block


@router.get("/sessions", response_model=list[PlannedSessionRead])
async def list_sessions(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlannedSession]:
    stmt = select(PlannedSession).where(PlannedSession.user_id == current_user.id)
    if start_date:
        stmt = stmt.where(PlannedSession.scheduled_date >= start_date)
    if end_date:
        stmt = stmt.where(PlannedSession.scheduled_date <= end_date)
    stmt = stmt.order_by(PlannedSession.scheduled_date.asc(), PlannedSession.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.patch("/sessions/{session_id}", response_model=PlannedSessionRead)
async def update_session(
    session_id: int,
    body: PlannedSessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlannedSession:
    result = await db.execute(
        select(PlannedSession).where(
            and_(
                PlannedSession.id == session_id,
                PlannedSession.user_id == current_user.id,
            )
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # A genuine date move preserves the original plan date (first move only) and,
    # unless the caller set an explicit status, marks the session RESCHEDULED.
    moved = False
    if body.scheduled_date is not None and body.scheduled_date != session.scheduled_date:
        if session.original_scheduled_date is None:
            session.original_scheduled_date = session.scheduled_date
        session.scheduled_date = body.scheduled_date
        moved = True

    if body.status is not None:
        session.status = body.status
    elif moved:
        session.status = SessionStatus.RESCHEDULED

    await db.commit()
    await db.refresh(session)
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
    rx = await prescribe_for_athlete(
        db, current_user.id, cast(TrainingGoal, goal), planned_session=session
    )
    await db.refresh(session)
    return TodaySessionResponse(
        session=PlannedSessionRead.model_validate(session, from_attributes=True),
        prescription=rx.to_prescribed_content(),
    )
