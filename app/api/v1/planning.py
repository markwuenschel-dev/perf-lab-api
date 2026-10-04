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
    PlannedWeekProjection,
    PrescriptionRevisionRead,
    TodaySessionResponse,
    WeeklyTemplateSlot,
    WeekReview,
)
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.services import planning_projection_service, planning_service, week_review_service
from app.services.planning_service import create_block_with_sessions, get_today_session
from app.services.prescription_service import prescribe_and_issue

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


@router.get("/projection", response_model=PlannedWeekProjection)
async def get_planned_week_projection(
    through: date | None = Query(
        default=None,
        description=(
            "Last day to project (inclusive). Default: end of the current block week, or "
            "today+6 with no active block. Capped at 28 days from today."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlannedWeekProjection:
    """Pending planned sessions projected forward: sRPE load and modeled fatigue per day.

    Display-only (ADR-0073): writes nothing and feeds no scoring. Fatigue, not readiness.
    """
    try:
        return await planning_projection_service.planned_week_projection(
            db, current_user.id, through
        )
    except planning_projection_service.ProjectionWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@router.get("/week-review", response_model=WeekReview)
async def get_week_review(
    block_id: int | None = Query(default=None),
    week_number: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WeekReview:
    """One block week in review: its sessions, counts, what the state did, and the facts
    already determined for the week after. Display-only; reads nothing it could change.

    Defaults to the current block's current week. No active block, no state, or state that
    fails strict decoding answer ``200 {available: false, reason}`` — this surface gates
    nothing, so a decode failure is not a 409 here.
    """
    try:
        return await week_review_service.build_week_review(
            db, current_user.id, block_id=block_id, week_number=week_number
        )
    except week_review_service.WeekReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/today", response_model=TodaySessionResponse)
async def get_today(
    goal: str = Query(TRAINING_GOAL_DEFAULT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TodaySessionResponse:
    return await _today(db, current_user.id, goal, allow_relax=False)


@router.post("/today/recheck", response_model=TodaySessionResponse)
async def recheck_today(
    goal: str = Query(TRAINING_GOAL_DEFAULT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TodaySessionResponse:
    """The athlete asks for today's session to be re-checked (P1, fork 4).

    Same as ``GET /today``, except that a safety restriction which has CLEARED may now be
    lifted: the issued session is replaced by the unrestricted one. ``GET`` never relaxes an
    issued restriction on its own — it could raise the load of a workout already started.
    """
    return await _today(db, current_user.id, goal, allow_relax=True)


async def _today(
    db: AsyncSession, user_id: int, goal: str, *, allow_relax: bool
) -> TodaySessionResponse:
    session = await get_today_session(db, user_id)
    if not session:
        return TodaySessionResponse(session=None, prescription=None)

    # The single prescribe-and-issue seam (P1): serves today's issued revision, or issues
    # one, by the fork-4 rule — so every actionable surface reading /today gets the same
    # revision and identical content until safety requires a replacement.
    result = await prescribe_and_issue(
        db, user_id, cast(TrainingGoal, goal), planned_session=session, allow_relax=allow_relax
    )
    rx = result.prescription
    await db.refresh(session)
    revision = (
        PrescriptionRevisionRead.model_validate(result.revision).model_copy(
            update={"issued_now": result.issued_now}
        )
        if result.revision is not None
        else None
    )
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
        revision=revision,
    )
