from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.domain_vocab import block_goal_to_domain, canonical_domain
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.objective import Objective
from app.schemas.planning import BlockCreateRequest, WeeklyTemplateSlot
from app.services import macrocycle_service

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
    BlockGoal.HYPERTROPHY: [
        WeeklyTemplateSlot(day_of_week=1, category="High Volume Upper", modality="Hypertrophy"),
        WeeklyTemplateSlot(day_of_week=3, category="High Volume Lower", modality="Hypertrophy"),
        WeeklyTemplateSlot(day_of_week=5, category="Accessory / Isolation", modality="Hypertrophy"),
    ],
    BlockGoal.POWER: [
        WeeklyTemplateSlot(day_of_week=1, category="Power Development", modality="Power"),
        WeeklyTemplateSlot(day_of_week=3, category="Strength Potentiation", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=5, category="Neural Priming", modality="Power"),
    ],
    BlockGoal.HYROX: [
        WeeklyTemplateSlot(day_of_week=1, category="Strength Endurance", modality="Mixed"),
        WeeklyTemplateSlot(day_of_week=3, category="Running + Functional", modality="Mixed"),
        WeeklyTemplateSlot(day_of_week=6, category="Hyrox Simulation", modality="Mixed"),
    ],
    BlockGoal.CROSSFIT: [
        WeeklyTemplateSlot(day_of_week=1, category="Strength + Skill", modality="Mixed"),
        WeeklyTemplateSlot(day_of_week=3, category="MetCon", modality="Conditioning"),
        WeeklyTemplateSlot(day_of_week=5, category="Engine Work", modality="Conditioning"),
    ],
    BlockGoal.CALISTHENICS: [
        WeeklyTemplateSlot(day_of_week=1, category="Skill & Straight-Arm Strength", modality="Calisthenics"),
        WeeklyTemplateSlot(day_of_week=3, category="Bodyweight Strength", modality="Calisthenics"),
        WeeklyTemplateSlot(day_of_week=5, category="Gymnastics Conditioning", modality="Calisthenics"),
    ],
    BlockGoal.GENERAL: [
        WeeklyTemplateSlot(day_of_week=1, category="Full-Body GPP", modality="General"),
        WeeklyTemplateSlot(day_of_week=3, category="Aerobic + Strength", modality="General"),
        WeeklyTemplateSlot(day_of_week=5, category="Active Recovery", modality="General"),
    ],
    BlockGoal.RECOMP: [
        WeeklyTemplateSlot(day_of_week=1, category="Strength Preservation", modality="Strength"),
        WeeklyTemplateSlot(day_of_week=3, category="Metabolic Conditioning", modality="Conditioning"),
        WeeklyTemplateSlot(day_of_week=5, category="Active Recovery", modality="General"),
    ],
}


def _default_template_for_goal(goal: BlockGoal, sessions_per_week: int) -> list[WeeklyTemplateSlot]:
    slots = _DEFAULT_TEMPLATES.get(goal) or _DEFAULT_TEMPLATES[BlockGoal.STRENGTH]
    return slots[:sessions_per_week]


# Canonical domain → a representative weekly slot (category, modality).
_DOMAIN_SLOT: dict[str, tuple[str, str]] = {
    "running": ("Aerobic Base", "Running"),
    "strength": ("Max Strength", "Strength"),
    "powerlifting": ("SBD Strength", "Strength"),
    "hypertrophy": ("High Volume", "Hypertrophy"),
    "power": ("Power Development", "Power"),
    "weightlifting": ("Weightlifting Technique", "Power"),
    "conditioning": ("Metabolic Conditioning", "Conditioning"),
    "mixed": ("Mixed Modal", "Mixed"),
    "calisthenics": ("Bodyweight Strength", "Calisthenics"),
    "gymnastics": ("Gymnastics Skill", "Calisthenics"),
    "grip": ("Grip & Support", "Strength"),
    "general": ("Full-Body GPP", "General"),
}
_WEEK_DAY_ORDER = (1, 3, 5, 2, 4, 6, 7)


def _template_from_modality_mix(
    modality_mix: dict[str, Any] | None,
    sessions_per_week: int,
) -> list[WeeklyTemplateSlot] | None:
    """Build a weekly template by distributing sessions across a domain mix (ADR-0030).

    `modality_mix` is a ``canonical-domain → weight`` map; sessions are allocated by
    largest remainder and spread across the week. Returns None when no usable mix is
    given, so the caller falls back to the goal default. This makes `modality_mix` the
    driver of concurrent multi-domain blocks rather than inert metadata.
    """
    if not modality_mix:
        return None
    weights = {canonical_domain(k): float(v) for k, v in modality_mix.items() if float(v) > 0}
    total = sum(weights.values())
    if total <= 0 or sessions_per_week <= 0:
        return None

    alloc: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for dom, w in weights.items():
        exact = sessions_per_week * w / total
        alloc[dom] = int(exact)
        assigned += alloc[dom]
        remainders.append((exact - int(exact), dom))

    remainders.sort(reverse=True)
    i = 0
    while assigned < sessions_per_week and remainders:
        alloc[remainders[i % len(remainders)][1]] += 1
        assigned += 1
        i += 1

    slots: list[WeeklyTemplateSlot] = []
    day_i = 0
    for dom, n in alloc.items():
        category, modality = _DOMAIN_SLOT.get(dom, _DOMAIN_SLOT["general"])
        for _ in range(n):
            slots.append(
                WeeklyTemplateSlot(
                    day_of_week=_WEEK_DAY_ORDER[day_i % len(_WEEK_DAY_ORDER)],
                    category=category,
                    modality=modality,
                )
            )
            day_i += 1
    slots.sort(key=lambda s: s.day_of_week)
    return slots[:sessions_per_week] or None


def select_block_macrocycle_id(
    candidates: list[tuple[int, str | None]], block_goal: str
) -> int | None:
    """Which active macrocycle a new block should hang under (Phase 5 spine).

    ``candidates`` is ``(macrocycle_id, anchor_objective_domain)`` for the user's
    ACTIVE macrocycles, already in deterministic order (start_date asc, id asc).

    - none → no program to attach to (NULL).
    - exactly one → that program (unambiguous — the original rule).
    - many → attach only when the block's goal domain UNIQUELY matches one
      program's anchor domain; a zero-match or a multi-match stays NULL rather
      than guess which program owns the block. This disambiguates the common
      case (e.g. a Powerlifting block joins the powerlifting-anchored program)
      without over-reaching.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][0]
    target = block_goal_to_domain(block_goal)
    matches = [mid for mid, dom in candidates if dom and canonical_domain(dom) == target]
    return matches[0] if len(matches) == 1 else None


async def create_block_with_sessions(
    db: AsyncSession,
    user_id: int,
    req: BlockCreateRequest,
) -> MesocycleBlock:
    weekly_template = (
        req.weekly_template
        or _template_from_modality_mix(req.modality_mix, req.sessions_per_week)
        or _default_template_for_goal(req.goal, req.sessions_per_week)
    )
    end_date = req.start_date + timedelta(days=req.duration_weeks * 7 - 1)

    # Auto-associate the new block with the user's macrocycle "spine" (Phase 5).
    # One active macrocycle → attach; several → disambiguate by matching the
    # block's goal domain to a program's anchor-objective domain (see
    # select_block_macrocycle_id). Server-side only (no request/response field).
    active_macrocycles = await macrocycle_service.list_macrocycles(db, user_id)
    candidates: list[tuple[int, str | None]] = []
    if active_macrocycles:
        anchor_ids = {m.objective_id for m in active_macrocycles}
        rows = await db.execute(
            select(Objective.id, Objective.domain).where(Objective.id.in_(anchor_ids))
        )
        domain_by_objective: dict[int, str | None] = {}
        for oid, dom in rows.all():
            domain_by_objective[oid] = dom
        candidates = [(m.id, domain_by_objective.get(m.objective_id)) for m in active_macrocycles]
    macrocycle_id = select_block_macrocycle_id(candidates, req.goal.value)

    block = MesocycleBlock(
        user_id=user_id,
        macrocycle_id=macrocycle_id,
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
        target_session_minutes=req.target_session_minutes,
        accessory_emphasis=req.accessory_emphasis,
        accessory_focus=req.accessory_focus,
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


async def count_block_skips(
    db: AsyncSession,
    user_id: int,
    block_id: int,
) -> int:
    """Count SKIPPED planned sessions in a block — an adherence signal the
    prescriber uses to bias toward lighter/variety work after repeated skips."""
    result = await db.execute(
        select(func.count())
        .select_from(PlannedSession)
        .where(
            and_(
                PlannedSession.user_id == user_id,
                PlannedSession.block_id == block_id,
                PlannedSession.status == SessionStatus.SKIPPED,
            )
        )
    )
    return int(result.scalar_one() or 0)


async def get_today_session(
    db: AsyncSession,
    user_id: int,
    for_date: date | None = None,
) -> PlannedSession | None:
    """The canonical "today's target session" for prescription.

    User-wide pending session on ``for_date`` (default today), lowest ``id`` wins.
    Both prescribe entry points resolve through this: ``/planning/today`` passes the
    result straight into ``prescribe_for_athlete``; ``/next-session`` lets the
    prescriber call it. Sharing one resolver is what makes them target the same row
    by construction (CONTEXT.md: prescribe-and-persist). The ``id`` ordering matches
    ``state_service._match_planned_session`` so the prescribed session equals the one
    a later log fulfills.
    """
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

