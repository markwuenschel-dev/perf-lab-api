from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.prescriber import recommend_next_session
from app.models.mesocycle import MesocycleBlock, PlannedSession, SessionStatus
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
from app.services.decision_telemetry import persist_prescription_decision
from app.services.objective_service import active_objective_signals
from app.services.planning_service import create_block_with_sessions, get_today_session
from app.services.state_service import load_current_state

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

    state = await load_current_state(db, current_user.id)
    if state is None:
        return TodaySessionResponse(
            session=PlannedSessionRead.model_validate(session, from_attributes=True),
            prescription=None,
        )

    profile_result = await db.execute(select(AthleteProfile).where(AthleteProfile.user_id == current_user.id))
    profile = profile_result.scalars().first()

    # Fetch the parent block so periodization (duration_weeks + deload cadence,
    # ADR-0029) applies on this path too — mirrors the block_context built by
    # prescription_service.prescribe_for_athlete for the /next-session path.
    # Without duration_weeks the envelope guard in recommend_next_session
    # silently no-ops (weeks_total == 0), so /planning/today and /next-session
    # disagreed on periodization.
    block_result = await db.execute(
        select(MesocycleBlock).where(MesocycleBlock.id == session.block_id)
    )
    block = block_result.scalars().first()

    # Objective taper + domain-emphasis (Phase 4a). This entry point builds
    # its own block_context separately from prescription_service — both must
    # carry the same objective signals (Phase 0/3a lesson: /today drifted
    # from /next-session's block_context before).
    objective_signals = await active_objective_signals(db, current_user.id)

    block_context = {
        "session_category": session.category,
        "is_deload": session.is_deload,
        "is_benchmark": session.is_benchmark,
        "week_number": session.week_number,
        "duration_weeks": block.duration_weeks if block else None,
        "deload_every_n_weeks": block.deload_every_n_weeks if block else None,
        "deload_volume_factor": block.deload_volume_factor if block else None,
        "target_session_minutes": block.target_session_minutes if block else None,
        "accessory_emphasis": block.accessory_emphasis if block else None,
        "accessory_focus": block.accessory_focus if block else None,
        "objective_taper": objective_signals["taper"],
        "objective_domain": objective_signals["domain"],
    }
    # candidate_log_out captures the full ranked pool for decision telemetry
    # (Workstream B); the prescriber only fills it, so selection is unchanged.
    candidate_log: list[SessionCandidate] = []
    rx = recommend_next_session(
        state,
        goal=goal,  # type: ignore[arg-type]
        block_context=block_context,
        available_equipment=(profile.equipment if profile else None),
        candidate_log_out=candidate_log,
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

    # Best-effort decision telemetry — after the prescription is committed so a
    # telemetry failure can never alter or block the response.
    await persist_prescription_decision(
        db,
        current_user.id,
        rx,
        candidate_log,
        goal=str(goal),
        decision_mode="adaptive",
        planned_session_id=session.id,
        state_snapshot=state.model_dump(mode="json"),
        block_context=block_context,
    )
    return TodaySessionResponse(
        session=PlannedSessionRead.model_validate(session, from_attributes=True),
        prescription=session.prescribed_content,
    )
