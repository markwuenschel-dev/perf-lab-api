from __future__ import annotations

from datetime import date, timedelta
from typing import Any, TypedDict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.domain_vocab import block_goal_to_domain, canonical_domain
from app.logic.planned_session_slots import ACTIVE_RECOVERY_CATEGORY, SPEED_CATEGORY
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.objective import Objective
from app.models.telemetry import SessionFeedback
from app.schemas.planning import (
    BlockCreateRequest,
    BlockUpdateRequest,
    PlannedSessionUpdateRequest,
    WeeklyTemplateSlot,
)
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


def _mix_slot(key: str) -> tuple[str, str, str]:
    """(canonical domain, category, modality) for one modality-mix key.

    A "sprinting" key is a running day of Speed work (phase 5.6). Its domain is running
    (ADR-0038), but canonicalizing FIRST would turn it into an Aerobic Base day and hand a
    sprint athlete a distance runner's week.
    """
    domain = canonical_domain(key)
    if key.strip().lower() == "sprinting":
        return domain, SPEED_CATEGORY, "Running"
    return (domain, *_DOMAIN_SLOT.get(domain, _DOMAIN_SLOT["general"]))


def _sprint_week(sessions: int) -> list[str]:
    """The categories of a sprint-intent week's running sessions, in day order.

    Two sessions are both Speed. From three up they alternate Speed / Active Recovery,
    starting and (for odd counts) ending on Speed, so quality sprint days are not stacked back
    to back by default: intensive sprint work generally needs about 48 h (Haugen et al.).
    This is a planner default, not sprint periodization. How acceleration and speed-endurance
    work are split across a block is phase 7.
    """
    if sessions <= 2:
        return [SPEED_CATEGORY] * sessions
    return [SPEED_CATEGORY if i % 2 == 0 else ACTIVE_RECOVERY_CATEGORY for i in range(sessions)]


def _template_from_modality_mix(
    modality_mix: dict[str, Any] | None,
    sessions_per_week: int,
) -> list[WeeklyTemplateSlot] | None:
    """Build a weekly template by distributing sessions across a domain mix (ADR-0030).

    `modality_mix` is a ``domain → weight`` map (``_mix_slot`` resolves each key); sessions are allocated by
    largest remainder and spread across the week. Returns None when no usable mix is
    given, so the caller falls back to the goal default. This makes `modality_mix` the
    driver of concurrent multi-domain blocks rather than inert metadata.
    """
    if not modality_mix:
        return None
    # Keyed by the slot a weight becomes, not by domain alone: "running" and "sprinting" share
    # a domain but are different days.
    weights: dict[tuple[str, str, str], float] = {}
    for k, v in modality_mix.items():
        if float(v) > 0:
            weights[_mix_slot(k)] = float(v)
    total = sum(weights.values())
    if total <= 0 or sessions_per_week <= 0:
        return None

    alloc: dict[tuple[str, str, str], int] = {}
    remainders: list[tuple[float, tuple[str, str, str]]] = []
    assigned = 0
    for slot_key, w in weights.items():
        exact = sessions_per_week * w / total
        alloc[slot_key] = int(exact)
        assigned += alloc[slot_key]
        remainders.append((exact - int(exact), slot_key))

    remainders.sort(reverse=True)
    i = 0
    while assigned < sessions_per_week and remainders:
        alloc[remainders[i % len(remainders)][1]] += 1
        assigned += 1
        i += 1

    slots: list[WeeklyTemplateSlot] = []
    day_i = 0
    for (dom, category, modality), n in alloc.items():
        for _ in range(n):
            slots.append(
                WeeklyTemplateSlot(
                    day_of_week=_WEEK_DAY_ORDER[day_i % len(_WEEK_DAY_ORDER)],
                    category=category,
                    modality=modality,
                    # The domain the weight was asked for, carried explicitly: `modality` is
                    # shared by several domains and cannot be turned back into this.
                    domain=dom,
                )
            )
            day_i += 1
    slots.sort(key=lambda s: s.day_of_week)
    # A sprinting weight is sprint INTENT, not "every session is Speed": its sessions get the
    # sprint-week pattern, in day order.
    sprint = [i for i, s in enumerate(slots) if s.category == SPEED_CATEGORY]
    for i, category in zip(sprint, _sprint_week(len(sprint)), strict=True):
        slots[i] = slots[i].model_copy(update={"category": category})
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


def preview_weekly_template(req: BlockCreateRequest) -> list[WeeklyTemplateSlot]:
    """The week a block WOULD be generated with — the same resolution `create` performs.

    Exists so the create screen can show the real allocation before committing, without
    reimplementing largest-remainder in TypeScript: a style that ends up with zero sessions is
    a fact the athlete should see, and a second implementation would eventually disagree with
    this one. Pure and persists nothing.
    """
    return (
        req.weekly_template
        or _template_from_modality_mix(req.modality_mix, req.sessions_per_week)
        or _default_template_for_goal(req.goal, req.sessions_per_week)
    )


async def create_block_with_sessions(
    db: AsyncSession,
    user_id: int,
    req: BlockCreateRequest,
) -> MesocycleBlock:
    weekly_template = preview_weekly_template(req)
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
        intensity=req.intensity,
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
                domain=slot.domain,
                status=SessionStatus.PENDING,
                is_deload=is_deload,
                is_benchmark=is_benchmark,
                benchmark_key="periodic_retest" if is_benchmark else None,
            )
            db.add(ps)

    await db.commit()
    await db.refresh(block)
    return block


class BlockAdherenceSignals(TypedDict):
    """Adherence evidence for one block — the prescriber's only adherence input.

    Two disjoint counts over the same session set, never two overlapping views of
    one session (ADR-0070).
    """

    recent_skips: int
    recent_modifications: int


async def block_adherence_signals(
    db: AsyncSession,
    user_id: int,
    block_id: int,
) -> BlockAdherenceSignals:
    """Skipped and athlete-modified sessions in a block, counted once each.

    One query, so the two counts cannot disagree about a session (ADR-0070).
    ``PlannedSession.status`` is the canonical source of *occurrence*:
    ``recent_skips`` comes from it alone, and a modification is counted only for a
    session that actually COMPLETED. A skipped session therefore contributes to
    exactly one count even when its feedback row also flags a modification —
    dropping the athlete's own report of a skip they also modified is the correct
    trade for never penalising one session twice.

    Recency is block membership, derived from the planned session, never from
    ``SessionFeedback.created_at``: submission time measures reporting behaviour,
    not training behaviour, and would make a replay non-reproducible.

    The LEFT JOIN cannot fan out — ``session_feedback.planned_session_id`` is
    unique — so each planned session yields exactly one row and the counts are
    deduplicated by construction rather than by a DISTINCT.
    """
    # A report of "modified" counts even when the athlete named no dimension.
    # Normalising here rather than refusing the write keeps an honest but
    # under-specified report as evidence: "I changed it" is friction whether or not
    # they said which part, and the alternative silently discards it.
    modified = or_(
        SessionFeedback.status == "modified",
        SessionFeedback.modified_volume.is_(True),
        SessionFeedback.modified_intensity.is_(True),
        SessionFeedback.modified_exercises.is_(True),
    )
    result = await db.execute(
        select(
            func.count().filter(PlannedSession.status == SessionStatus.SKIPPED),
            func.count().filter(
                and_(PlannedSession.status == SessionStatus.COMPLETED, modified)
            ),
        )
        .select_from(PlannedSession)
        .outerjoin(
            SessionFeedback,
            SessionFeedback.planned_session_id == PlannedSession.id,
        )
        .where(
            and_(
                PlannedSession.user_id == user_id,
                PlannedSession.block_id == block_id,
            )
        )
    )
    skips, modifications = result.one()
    return BlockAdherenceSignals(
        recent_skips=int(skips or 0),
        recent_modifications=int(modifications or 0),
    )


async def update_block(
    db: AsyncSession,
    user_id: int,
    block_id: int,
    payload: BlockUpdateRequest,
) -> MesocycleBlock | None:
    """Partial update of one owned block. Returns ``None`` when the block does not
    exist or is not owned by ``user_id`` (router → 404, house idiom).

    ``is not None`` per-field semantics, preserved exactly from the pre-refactor
    router: an explicit JSON ``null`` and an omitted field are indistinguishable —
    both leave the stored value untouched. This is deliberately NOT
    ``payload.model_dump(exclude_unset=True)`` (the idiom ``update_macrocycle``
    uses), which would apply an explicit null and silently change behavior.
    """
    result = await db.execute(
        select(MesocycleBlock).where(
            and_(
                MesocycleBlock.id == block_id,
                MesocycleBlock.user_id == user_id,
            )
        )
    )
    block = result.scalars().first()
    if not block:
        return None
    if payload.status is not None:
        block.status = payload.status
    if payload.rationale is not None:
        block.rationale = payload.rationale
    if payload.modality_mix is not None:
        block.modality_mix = payload.modality_mix
    if payload.deload_volume_factor is not None:
        block.deload_volume_factor = payload.deload_volume_factor
    await db.commit()
    await db.refresh(block)
    return block


async def list_blocks(db: AsyncSession, user_id: int) -> list[MesocycleBlock]:
    """All of a user's blocks, newest first. No id tiebreaker — preserved exactly
    from the pre-refactor router; do not import ``list_sessions``' tiebreak here."""
    result = await db.execute(
        select(MesocycleBlock)
        .where(MesocycleBlock.user_id == user_id)
        .order_by(MesocycleBlock.created_at.desc())
    )
    return list(result.scalars().all())


async def list_sessions(
    db: AsyncSession,
    user_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[PlannedSession]:
    """A user's planned sessions, optionally date-bounded. ``scheduled_date asc, id
    asc`` — preserved exactly from the pre-refactor router."""
    stmt = select(PlannedSession).where(PlannedSession.user_id == user_id)
    if start_date:
        stmt = stmt.where(PlannedSession.scheduled_date >= start_date)
    if end_date:
        stmt = stmt.where(PlannedSession.scheduled_date <= end_date)
    stmt = stmt.order_by(PlannedSession.scheduled_date.asc(), PlannedSession.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_session(
    db: AsyncSession,
    user_id: int,
    session_id: int,
    payload: PlannedSessionUpdateRequest,
) -> PlannedSession | None:
    """Partial update of one owned planned session. Returns ``None`` when the
    session does not exist or is not owned by ``user_id`` (router → 404)."""
    result = await db.execute(
        select(PlannedSession).where(
            and_(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        )
    )
    session = result.scalars().first()
    if not session:
        return None

    # A genuine date move preserves the original plan date (first move only). It does
    # NOT change lifecycle status: the auto-transition to RESCHEDULED used to make the
    # session permanently undiscoverable, because both resolvers filter on PENDING
    # (planning_service.get_today_session, state_service._match_planned_session) and
    # nothing ever writes a session back to PENDING. See ADR-0069.
    if payload.scheduled_date is not None and payload.scheduled_date != session.scheduled_date:
        if session.original_scheduled_date is None:
            session.original_scheduled_date = session.scheduled_date
        session.scheduled_date = payload.scheduled_date

    if payload.status is not None:
        session.status = payload.status

    await db.commit()
    await db.refresh(session)
    return session


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

