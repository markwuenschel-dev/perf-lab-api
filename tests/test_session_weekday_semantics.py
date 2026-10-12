"""A planned session keeps its block-relative slot and records a real weekday (P5a, ADR-0074).

Block creation places a session on ``start_date + (week - 1) * 7 + (slot - 1)``: a template
slot is day N of the block week, not a weekday. P5a keeps every date and every scheduling rule
exactly as before and only corrects what a session records:

* ``block_day``: the slot it was generated from (the value ``day_of_week`` used to hold);
* ``day_of_week``: the ISO weekday of its plan date (``COALESCE(original_scheduled_date,
  scheduled_date)``), which a move does not change.

The parity tests compare creation against an oracle that restates the pre-P5a rules on its own
(``_old_rules``), over all seven start weekdays, every goal's default week at 1-7 sessions,
custom templates (unsorted, duplicated days) and a style-mix week, through benchmark and deload
weeks.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import User
from app.schemas.planning import BlockCreateRequest, PlannedSessionUpdateRequest, WeeklyTemplateSlot
from app.services.planning_service import (
    create_block_with_sessions,
    preview_weekly_template,
    update_session,
)

pytestmark = pytest.mark.asyncio

# Monday 2026-10-12 .. Sunday 2026-10-18, all in the future relative to the template rules (the
# generator never looks at today). Moves in the reschedule test use dates after today.
MONDAY = date(2026, 10, 12)
START_WEEKDAYS = [MONDAY + timedelta(days=i) for i in range(7)]

# Five weeks: benchmark weeks 2 and 4, deload week 3, plain weeks 1 and 5.
WEEKS, BENCHMARK_EVERY, DELOAD_EVERY = 5, 2, 3


@dataclass(frozen=True)
class Expected:
    scheduled_date: date
    week_number: int
    block_day: int
    category: str
    modality: str
    domain: str | None
    is_deload: bool
    is_benchmark: bool
    benchmark_key: str | None


def _old_rules(start: date, template: list[WeeklyTemplateSlot]) -> list[Expected]:
    """The sessions creation produced before P5a, in creation order, restated independently:
    template order within each week; a slot is an offset from the block start; the benchmark is
    every slot sharing the LAST slot's day in a benchmark week; deload is by week number."""
    out: list[Expected] = []
    for week in range(1, WEEKS + 1):
        for slot in template:
            bench = week % BENCHMARK_EVERY == 0 and slot.day_of_week == template[-1].day_of_week
            out.append(Expected(
                scheduled_date=start + timedelta(days=7 * (week - 1) + slot.day_of_week - 1),
                week_number=week,
                block_day=slot.day_of_week,
                category="Benchmark Session" if bench else slot.category,
                modality=slot.modality,
                domain=slot.domain,
                is_deload=week % DELOAD_EVERY == 0,
                is_benchmark=bench,
                benchmark_key="periodic_retest" if bench else None,
            ))
    return out


def _request(start: date, goal: BlockGoal, n: int, **kw) -> BlockCreateRequest:
    return BlockCreateRequest(
        goal=goal, start_date=start, duration_weeks=WEEKS, sessions_per_week=n,
        deload_every_n_weeks=DELOAD_EVERY, benchmark_every_n_weeks=BENCHMARK_EVERY, **kw,
    )


async def _user(db, email: str) -> int:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.flush()
    return u.id


async def _check_creation(db, uid: int, req: BlockCreateRequest) -> list[str]:
    """Create the block and return every way it differs from the old rules plus the new labels."""
    template = preview_weekly_template(req)
    block = await create_block_with_sessions(db, uid, req)
    rows = (await db.execute(
        select(PlannedSession).where(PlannedSession.block_id == block.id).order_by(PlannedSession.id)
    )).scalars().all()
    label = f"{req.goal.value} n={req.sessions_per_week} start={req.start_date:%a}"
    problems: list[str] = []
    # The stored template is the relative one creation consumed: no rotation to weekdays.
    if block.weekly_template != [s.model_dump() for s in template]:
        problems.append(f"{label}: stored template differs from the preview")
    expected = _old_rules(req.start_date, template)
    actual = [
        Expected(r.scheduled_date, r.week_number, r.block_day, r.category, r.modality, r.domain,
                 r.is_deload, r.is_benchmark, r.benchmark_key)
        for r in rows
    ]
    if actual != expected:
        problems.append(f"{label}: sessions differ from the old rules")
    for r in rows:
        if r.day_of_week != r.scheduled_date.isoweekday():
            problems.append(f"{label}: session {r.id} day_of_week {r.day_of_week} "
                            f"is not {r.scheduled_date:%A}")
        if r.original_scheduled_date is not None:
            problems.append(f"{label}: a new session has an original date")
    return problems


@pytest.mark.parametrize("start", START_WEEKDAYS, ids=lambda d: f"{d:%a}")
async def test_every_default_week_keeps_its_dates_and_rules_and_records_real_weekdays(
    async_db, start: date
) -> None:
    uid = await _user(async_db, f"weekday-defaults-{start:%a}@test.com")
    problems: list[str] = []
    for goal in BlockGoal:
        for n in range(1, 8):
            problems += await _check_creation(async_db, uid, _request(start, goal, n))
    assert problems == []


CUSTOM_TEMPLATES = {
    "unsorted": [(5, "C"), (1, "A"), (3, "B")],
    "duplicate days": [(2, "A"), (2, "B"), (6, "C")],
    "last slot is not the latest day": [(6, "A"), (2, "B")],
    "seven days, reversed": [(d, f"D{d}") for d in range(7, 0, -1)],
    "one slot on day 7": [(7, "A")],
}


@pytest.mark.parametrize("start", START_WEEKDAYS, ids=lambda d: f"{d:%a}")
async def test_custom_and_style_mix_weeks_keep_their_dates_and_rules(async_db, start: date) -> None:
    uid = await _user(async_db, f"weekday-custom-{start:%a}@test.com")
    problems: list[str] = []
    for slots in CUSTOM_TEMPLATES.values():
        template = [WeeklyTemplateSlot(day_of_week=d, category=c, modality="Strength") for d, c in slots]
        problems += await _check_creation(
            async_db, uid, _request(start, BlockGoal.STRENGTH, len(template), weekly_template=template)
        )
    problems += await _check_creation(
        async_db, uid,
        _request(start, BlockGoal.RUNNING, 5, modality_mix={"sprinting": 0.6, "strength": 0.4}),
    )
    assert problems == []


async def _sessions(db, block_id: int) -> list[PlannedSession]:
    return list((await db.execute(
        select(PlannedSession).where(PlannedSession.block_id == block_id).order_by(PlannedSession.id)
    )).scalars().all())


async def test_a_thursday_strength_block_trains_thursday_saturday_monday(async_db) -> None:
    """The case the audit named: the preview said Mon/Wed/Fri; the dates were always Thu/Sat/Mon.
    The dates stay; the session now says so."""
    uid = await _user(async_db, "weekday-thursday@test.com")
    thursday = date(2026, 10, 15)
    block = await create_block_with_sessions(
        async_db, uid, _request(thursday, BlockGoal.STRENGTH, 3)
    )
    week2 = [s for s in await _sessions(async_db, block.id) if s.week_number == 2]
    assert [(s.scheduled_date, s.block_day, s.day_of_week) for s in week2] == [
        (date(2026, 10, 22), 1, 4),  # Thursday
        (date(2026, 10, 24), 3, 6),  # Saturday
        (date(2026, 10, 26), 5, 1),  # Monday
    ]
    # The benchmark is still the week's last slot: the Monday session, not a Wednesday.
    assert [s.is_benchmark for s in week2] == [False, False, True]


async def test_a_running_block_still_starts_the_day_after_its_start_date(async_db) -> None:
    """Running's authored week begins at slot 2, so its first session is the day after the start,
    whatever weekday that is."""
    uid = await _user(async_db, "weekday-running@test.com")
    for start in START_WEEKDAYS:
        block = await create_block_with_sessions(
            async_db, uid, _request(start, BlockGoal.RUNNING, 3)
        )
        first = (await _sessions(async_db, block.id))[0]
        assert (first.scheduled_date, first.block_day) == (start + timedelta(days=1), 2)
        assert first.day_of_week == (start + timedelta(days=1)).isoweekday()


async def test_a_move_changes_the_date_only(async_db) -> None:
    """Rescheduling keeps the slot and the plan date's weekday; the new date is scheduled_date's."""
    uid = await _user(async_db, "weekday-move@test.com")
    start = date.today() + timedelta(days=10)
    block = await create_block_with_sessions(async_db, uid, _request(start, BlockGoal.STRENGTH, 3))
    await async_db.commit()
    session = (await _sessions(async_db, block.id))[1]
    planned, slot, weekday, week = (
        session.scheduled_date, session.block_day, session.day_of_week, session.week_number
    )

    for days in (1, 3):  # two moves: the first records the original date, the second keeps it
        moved = await update_session(
            async_db, uid, session.id,
            PlannedSessionUpdateRequest(scheduled_date=planned + timedelta(days=days)),
        )
        await async_db.commit()
        assert moved is not None
        assert moved.scheduled_date == planned + timedelta(days=days)
        assert moved.original_scheduled_date == planned
        assert (moved.block_day, moved.day_of_week, moved.week_number) == (slot, weekday, week)
        assert moved.day_of_week == planned.isoweekday()
        assert moved.day_of_week != moved.scheduled_date.isoweekday()


async def test_the_database_refuses_out_of_range_days(async_db) -> None:
    uid = await _user(async_db, "weekday-checks@test.com")
    block = await create_block_with_sessions(async_db, uid, _request(MONDAY, BlockGoal.STRENGTH, 1))
    await async_db.commit()
    sid = (await _sessions(async_db, block.id))[0].id
    for column, value, constraint in (
        ("block_day", 0, "ck_planned_sessions_block_day"),
        ("block_day", 8, "ck_planned_sessions_block_day"),
        ("day_of_week", 0, "ck_planned_sessions_day_of_week"),
        ("day_of_week", 8, "ck_planned_sessions_day_of_week"),
    ):
        with pytest.raises(Exception, match=constraint):
            async with async_db.begin_nested():
                await async_db.execute(
                    text(f"UPDATE planned_sessions SET {column} = :v WHERE id = :i"),
                    {"v": value, "i": sid},
                )


async def test_block_creation_writes_both_days_explicitly() -> None:
    """block_day has no default: a writer that forgot it fails instead of inventing a slot."""
    column = MesocycleBlock.metadata.tables["planned_sessions"].c.block_day
    assert column.nullable is False
    assert column.default is None and column.server_default is None
