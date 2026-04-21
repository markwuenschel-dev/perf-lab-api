from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.schemas.planning import BlockCreateRequest, WeeklyTemplateSlot


_DEFAULT_TEMPLATES: dict[BlockGoal, list[WeeklyTemplateSlot]] = {
    BlockGoal.STRENGTH: [
        WeeklyTemplateSlot(day_of_week=1, category="Max Strength", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=3, category="Strength — Volume", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=5, category="Accessory Focus", modality="Hypertrophy"),
    ],
    BlockGoal.RUNNING: [
        WeeklyTemplateSlot(day_of_week=2, category="Aerobic Base", modality="Running"),
        WeeklyTemplateSlot(day_of_week=4, category="Threshold Work", modality="Running"),
        WeeklyTemplateSlot(day_of_week=6, category="Active Recovery", modality="Running"),
    ],
}


def _default_template_for_goal(goal: BlockGoal, sessions_per_week: int) -> list[WeeklyTemplateSlot]:
    slots = _DEFAULT_TEMPLATES.get(goal) or _DEFAULT_TEMPLATES[BlockGoal.STRENGTH]
    return slots[:sessions_per_week]


async def create_block_with_sessions(
    db: AsyncSession,
    user_id: int,
    req: BlockCreateRequest,
) -> MesocycleBlock:
    weekly_template = req.weekly_template or _default_template_for_goal(req.goal, req.sessions_per_week)
    end_date = req.start_date + timedelta(days=req.duration_weeks * 7 - 1)

    block = MesocycleBlock(
        user_id=user_id,
        goal=req.goal,
        status=BlockStatus.ACTIVE,
        duration_weeks=req.duration_weeks,
        sessions_per_week=req.sessions_per_week,
        start_date=req.start_date,
        end_date=end_date,
        modality_mix=req.modality_mix,
        weekly_template=[s.model_dump() for s in weekly_template],
        rationale=req.rationale,
        deload_every_n_weeks=req.deload_every_n_weeks,
        deload_volume_factor=req.deload_volume_factor,
    )
    db.add(block)
    await db.flush()

    benchmark_stride = req.benchmark_every_n_weeks or 0
    for week in range(1, req.duration_weeks + 1):
        is_deload = week % req.deload_every_n_weeks == 0
        week_start = req.start_date + timedelta(days=(week - 1) * 7)
        for slot in weekly_template:
            scheduled = week_start + timedelta(days=slot.day_of_week - 1)
            is_benchmark = bool(benchmark_stride and week % benchmark_stride == 0 and slot.day_of_week == weekly_template[-1].day_of_week)
            ps = PlannedSession(
                block_id=block.id,
                user_id=user_id,
                scheduled_date=scheduled,
                week_number=week,
                day_of_week=slot.day_of_week,
                category="Benchmark Session" if is_benchmark else slot.category,
                modality=slot.modality,
                status=SessionStatus.PENDING,
                is_deload=is_deload,
                is_benchmark=is_benchmark,
                benchmark_key="periodic_retest" if is_benchmark else None,
            )
            db.add(ps)

    await db.commit()
    await db.refresh(block)
    return block


async def get_today_session(
    db: AsyncSession,
    user_id: int,
    for_date: date | None = None,
) -> PlannedSession | None:
    d = for_date or date.today()
    result = await db.execute(
        select(PlannedSession)
        .where(
            and_(
                PlannedSession.user_id == user_id,
                PlannedSession.scheduled_date == d,
                PlannedSession.status == SessionStatus.PENDING,
            )
        )
        .order_by(PlannedSession.id.asc())
        .limit(1)
    )
    return result.scalars().first()

