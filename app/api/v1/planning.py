from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescriber import recommend_next_session
from app.models.athlete_state import AthleteState
from app.models.mesocycle import MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.schemas.planning import (
    BlockCreateRequest,
    BlockRead,
    BlockUpdateRequest,
    PlannedSessionRead,
    PlannedSessionUpdateRequest,
    TodaySessionResponse,
)
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.planning_service import create_block_with_sessions, get_today_session

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
    if body.status is not None:
        session.status = body.status
    if body.scheduled_date is not None:
        session.scheduled_date = body.scheduled_date
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

    state_result = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == current_user.id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    state_row = state_result.scalars().first()
    if not state_row:
        return TodaySessionResponse(session=session, prescription=None)
    state = unified_from_athlete_row(state_row)

    profile_result = await db.execute(select(AthleteProfile).where(AthleteProfile.user_id == current_user.id))
    profile = profile_result.scalars().first()

    # Deload sessions scale prescribed volume by the parent block's factor.
    deload_volume_factor: float | None = None
    if session.is_deload:
        factor_result = await db.execute(
            select(MesocycleBlock.deload_volume_factor).where(MesocycleBlock.id == session.block_id)
        )
        deload_volume_factor = factor_result.scalar_one_or_none()

    rx = recommend_next_session(
        state,
        goal=goal,  # type: ignore[arg-type]
        block_context={
            "session_category": session.category,
            "is_deload": session.is_deload,
            "is_benchmark": session.is_benchmark,
            "week_number": session.week_number,
            "deload_volume_factor": deload_volume_factor,
        },
        available_equipment=(profile.equipment if profile else None),
    )
    session.prescribed_content = {
        "type": rx.type,
        "focus": rx.focus,
        "rationale": rx.rationale,
        "duration_min": rx.duration_min,
        "model_version": rx.model_version,
        "exercises": [x.model_dump() for x in rx.exercises],
        "why": rx.why.model_dump() if rx.why else None,
    }
    await db.commit()
    await db.refresh(session)
    return TodaySessionResponse(
        session=session,
        prescription=session.prescribed_content,
    )
