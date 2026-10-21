"""C1b planned-week projection service (ADR-0073).

Pure rollout tests drive ``project_planned_days`` with transient ORM objects; the DB tests go
through ``planned_week_projection`` against a real schema to pin session selection (PENDING
only, membership by ``scheduled_date``) and the unavailable reasons.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from planning_helpers import block_day_of

from app.engine.simulate import baseline_state
from app.models.athlete_state import AthleteState
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.user import User
from app.services import state_service
from app.services.planning_projection_service import (
    MAX_WINDOW_DAYS,
    ProjectionWindowError,
    planned_week_projection,
    project_planned_days,
    resolve_window,
)

D0 = date(2026, 9, 28)  # a Monday


def _block(**kw) -> MesocycleBlock:
    params = {
        "id": 1,
        "user_id": 1,
        "goal": BlockGoal.STRENGTH,
        "status": BlockStatus.ACTIVE,
        "start_date": D0,
        "duration_weeks": 4,
        "deload_volume_factor": 0.6,
        "target_session_minutes": None,
    }
    params.update(kw)
    return MesocycleBlock(**params)


def _session(sid: int, day: date, **kw) -> PlannedSession:
    params = {
        "id": sid,
        "block_id": 1,
        "user_id": 1,
        "scheduled_date": day,
        "week_number": 1,
        "block_day": block_day_of(D0, day),  # _block()'s default start
        "day_of_week": day.isoweekday(),
        "category": "Heavy Lower",
        "modality": "Strength",
        "domain": "strength",
        "status": SessionStatus.PENDING,
        "is_deload": False,
        "prescribed_content": None,
    }
    params.update(kw)
    return PlannedSession(**params)


def _state():
    return baseline_state(when=datetime(2026, 9, 28, 0, 0))


# ── pure rollout ──────────────────────────────────────────────────────────────


def test_rollout_is_deterministic() -> None:
    block = _block()
    plan = {D0: [(_session(1, D0), block)], D0 + timedelta(days=2): [(_session(2, D0), block)]}
    a = project_planned_days(_state(), D0, D0 + timedelta(days=6), plan)
    b = project_planned_days(_state(), D0, D0 + timedelta(days=6), plan)
    assert a == b


def test_planned_day_loads_and_rest_day_recovers() -> None:
    block = _block()
    days, peak = project_planned_days(_state(), D0, D0 + timedelta(days=2), {D0: [(_session(1, D0), block)]})

    assert [d.date for d in days] == [D0, D0 + timedelta(days=1), D0 + timedelta(days=2)]
    assert days[0].load > 0
    assert days[0].sessions[0].planned_session_id == 1
    assert days[0].sessions[0].basis == "template_estimate"
    assert days[0].mean_fatigue > 0  # fatigue rose from a zero-fatigue baseline
    assert days[1].load == 0 and days[1].sessions == []
    assert days[1].mean_fatigue < days[0].mean_fatigue  # rest day recovers
    assert days[2].mean_fatigue < days[1].mean_fatigue
    assert peak == days[0].mean_fatigue


def test_deload_session_projects_lower_load_than_a_normal_one() -> None:
    block = _block()
    normal, _ = project_planned_days(_state(), D0, D0, {D0: [(_session(1, D0), block)]})
    deload, _ = project_planned_days(_state(), D0, D0, {D0: [(_session(1, D0, is_deload=True), block)]})
    assert deload[0].load < normal[0].load


def test_aware_state_timestamp_is_normalized_to_naive_utc() -> None:
    """State timestamps are naive in the DB; an aware one must not raise on subtraction."""
    aware = baseline_state(when=datetime(2026, 9, 27, 0, 0, tzinfo=UTC))
    days, _ = project_planned_days(aware, D0, D0 + timedelta(days=1), {D0: [(_session(1, D0), _block())]})
    assert len(days) == 2


def test_stale_state_is_caught_up_before_projecting() -> None:
    """Fatigue carried from a week-old state has decayed by the start of the window."""
    tired = _state()
    tired = tired.model_copy(
        update={
            "timestamp": datetime(2026, 9, 21, 0, 0),
            "fatigue_f": tired.fatigue_f.model_copy(update={"muscular": 60.0, "cns": 60.0}),
        }
    )
    fresh_tired = tired.model_copy(update={"timestamp": datetime(2026, 9, 28, 0, 0)})

    stale_days, _ = project_planned_days(tired, D0, D0, {})
    fresh_days, _ = project_planned_days(fresh_tired, D0, D0, {})
    assert stale_days[0].mean_fatigue < fresh_days[0].mean_fatigue


# ── window ────────────────────────────────────────────────────────────────────


def test_window_defaults_to_end_of_current_block_week() -> None:
    block = _block(id=7, start_date=D0 - timedelta(days=9))  # today is week 2, day 3
    w = resolve_window(block, D0, None)
    assert (w.block_id, w.week_number) == (7, 2)
    assert w.start == D0
    assert w.end == block.start_date + timedelta(days=13)


def test_window_without_block_is_one_week_and_through_is_capped() -> None:
    w = resolve_window(None, D0, None)
    assert (w.end, w.block_id, w.week_number) == (D0 + timedelta(days=6), None, None)

    capped = resolve_window(None, D0, D0 + timedelta(days=90))
    assert capped.end == D0 + timedelta(days=MAX_WINDOW_DAYS - 1)

    with pytest.raises(ProjectionWindowError):
        resolve_window(None, D0, D0 - timedelta(days=1))


def test_block_that_has_not_started_has_no_current_week() -> None:
    w = resolve_window(_block(start_date=D0 + timedelta(days=3)), D0, None)
    assert (w.block_id, w.week_number, w.end) == (None, None, D0 + timedelta(days=6))


# ── DB: selection + availability ──────────────────────────────────────────────


async def _user(db, email: str) -> int:
    user = User(email=email, hashed_password="x")
    db.add(user)
    await db.commit()
    return user.id


async def _db_block(db, uid: int, start: date) -> MesocycleBlock:
    block = MesocycleBlock(
        user_id=uid,
        goal=BlockGoal.STRENGTH,
        status=BlockStatus.ACTIVE,
        start_date=start,
        duration_weeks=4,
        sessions_per_week=3,
        weekly_template=[],
        modality_mix={},
        deload_every_n_weeks=4,
        deload_volume_factor=0.6,
    )
    db.add(block)
    await db.commit()
    return block


async def _db_session(db, uid: int, block: MesocycleBlock, day: date, **kw) -> PlannedSession:
    params = {
        "block_id": block.id,
        "user_id": uid,
        "scheduled_date": day,
        "week_number": 1,
        "block_day": block_day_of(block.start_date, day),
        "day_of_week": day.isoweekday(),
        "category": "Heavy Lower",
        "modality": "Strength",
        "domain": "strength",
        "status": SessionStatus.PENDING,
    }
    params.update(kw)
    s = PlannedSession(**params)
    db.add(s)
    await db.commit()
    return s


async def test_only_pending_sessions_are_projected_and_moves_count_by_date(async_db) -> None:
    uid = await _user(async_db, "c1b-select@t.io")
    await state_service.initialize_athlete_state(async_db, uid)
    block = await _db_block(async_db, uid, D0)
    pending = await _db_session(async_db, uid, block, D0)
    await _db_session(async_db, uid, block, D0 + timedelta(days=1), status=SessionStatus.COMPLETED)
    await _db_session(async_db, uid, block, D0 + timedelta(days=2), status=SessionStatus.SKIPPED)
    # Planned for week 2 but moved into this week: counted where it now sits.
    moved = await _db_session(
        async_db, uid, block, D0 + timedelta(days=4),
        week_number=2, original_scheduled_date=D0 + timedelta(days=8),
    )

    proj = await planned_week_projection(async_db, uid, today=D0)

    assert proj.available and proj.reason is None
    assert (proj.window.block_id, proj.window.week_number) == (block.id, 1)
    assert [d.date for d in proj.days] == [D0 + timedelta(days=i) for i in range(7)]
    by_day = {d.date: [s.planned_session_id for s in d.sessions] for d in proj.days}
    assert by_day[D0] == [pending.id]
    assert by_day[D0 + timedelta(days=1)] == []  # completed: already in state
    assert by_day[D0 + timedelta(days=2)] == []  # skipped
    assert by_day[D0 + timedelta(days=4)] == [moved.id]
    assert proj.peak_mean_fatigue == max(d.mean_fatigue for d in proj.days)


async def test_no_state_is_unavailable_not_initialized(async_db) -> None:
    uid = await _user(async_db, "c1b-nostate@t.io")

    proj = await planned_week_projection(async_db, uid, today=D0)

    assert (proj.available, proj.reason, proj.days) == (False, "no_state", [])
    assert await state_service.has_state(async_db, uid) is False  # read-only


async def test_undecodable_state_is_unavailable(async_db) -> None:
    uid = await _user(async_db, "c1b-invalid@t.io")
    async_db.add(
        AthleteState(
            user_id=uid,
            timestamp=datetime(2026, 9, 1, 12, 0),
            c_met_aerobic=300.0,
            c_nm_force=1400.0,
            c_struct=100.0,
            b_met_anaerobic=15000.0,
            f_met_systemic=5.0,
            f_nm_peripheral=5.0,
            f_nm_central=5.0,
            f_struct_damage=5.0,
            s_struct_signal=0.0,
            habit_strength=0.5,
            skill_state={},
            engine_state={"version": 2, "x": {}, "f": {}, "t": {}},
        )
    )
    await async_db.commit()

    proj = await planned_week_projection(async_db, uid, today=D0)

    assert (proj.available, proj.reason) == (False, "state_invalid")


# ── the athlete's days (B1) ───────────────────────────────────────────────────


def _fatigued(when: datetime):
    state = baseline_state(when=when)
    for k in state.fatigue_f.KEYS:
        setattr(state.fatigue_f, k, 30.0)
    return state


def test_a_fixed_offset_zone_projects_the_utc_days_shifted() -> None:
    """Brisbane (UTC+10, no DST): its local days are UTC days moved 10 hours earlier, so the
    same plan from the same local moment must project exactly what UTC projects. A rollout
    that kept UTC days would run a Brisbane day from 10:00 to 10:00 local instead."""
    from app.logic.athlete_calendar import UNKNOWN, AthleteCalendar
    block = _block()
    plan = {D0: [(_session(1, D0), block)], D0 + timedelta(days=2): [(_session(2, D0), block)]}
    brisbane = AthleteCalendar("Australia/Brisbane")
    local, peak_local = project_planned_days(
        _fatigued(datetime(2026, 9, 27, 14, 0)), D0, D0 + timedelta(days=6), plan, brisbane)
    utc, peak_utc = project_planned_days(
        _fatigued(datetime(2026, 9, 28, 0, 0)), D0, D0 + timedelta(days=6), plan, UNKNOWN)
    assert local == utc and peak_local == peak_utc
    # Under the UTC days the same Brisbane state would first decay 10 hours to the UTC
    # midnight, a different (lower) fatigue on day one: the bug this pins.
    shifted, _ = project_planned_days(
        _fatigued(datetime(2026, 9, 27, 14, 0)), D0, D0 + timedelta(days=6), plan, UNKNOWN)
    assert shifted[0].mean_fatigue != local[0].mean_fatigue


@pytest.mark.parametrize(("day", "hours", "fatigue"), [
    (date(2026, 3, 8), 23, 23.06),   # New York spring forward: a 23-hour day decays less
    (date(2026, 11, 1), 25, 22.63),  # fall back: a 25-hour day decays more
    (date(2026, 3, 9), 24, 22.84),   # an ordinary day
])
def test_a_dst_day_is_simulated_for_its_real_length(day: date, hours: int, fatigue: float) -> None:
    """A New York day runs from local midnight to local midnight: 23 h, 25 h or 24 h. The day-end
    state equals one engine rest step of exactly that length, and the numbers are pinned."""
    from app.engine.simulate import make_log, rest_dose
    from app.logic.athlete_calendar import AthleteCalendar
    from app.logic.constraint_engine.candidate import mean_fatigue
    from app.services import planning_projection_service as pps
    ny = AthleteCalendar("America/New_York")
    start, end = ny.day_start_utc(day), ny.day_end_utc(day)
    assert end - start == timedelta(hours=hours)
    days, _ = project_planned_days(_fatigued(start), day, day, {}, ny)
    one_step = pps.update_athlete_state(
        _fatigued(start), rest_dose(), end - start,
        make_log(end, pps._REST_MODALITY, session_rpe=1.0))
    assert days[0].mean_fatigue == round(mean_fatigue(one_step), 2) == fatigue


def test_sessions_sit_at_the_local_session_hour() -> None:
    """The slot is SESSION_HOUR on the athlete's wall clock, on both sides of a DST change."""
    from app.logic.athlete_calendar import AthleteCalendar
    from app.services import planning_projection_service as pps
    ny = AthleteCalendar("America/New_York")
    winter, summer = date(2026, 3, 7), date(2026, 3, 9)
    assert ny.at_utc(winter, pps.time(pps.SESSION_HOUR, 0)) == datetime(2026, 3, 7, 17, 0)
    assert ny.at_utc(summer, pps.time(pps.SESSION_HOUR, 0)) == datetime(2026, 3, 9, 16, 0)


def test_a_zone_at_utc_plus_13_starts_its_first_day_at_its_own_midnight() -> None:
    """Auckland in summer (NZDT, UTC+13): its local noon is the previous UTC day. The first day
    must start at Auckland's midnight (Jan 11 11:00Z); starting it at the UTC midnight would put
    the noon session before the day began and clamp its decay (28.76 instead of 28.67)."""
    from app.logic.athlete_calendar import UNKNOWN, AthleteCalendar
    auckland = AthleteCalendar("Pacific/Auckland")
    day = date(2026, 1, 12)
    block = _block(start_date=day)
    plan = {day: [(_session(1, day, block_day=1), block)]}
    start = auckland.day_start_utc(day)
    assert start == datetime(2026, 1, 11, 11, 0)
    local, _ = project_planned_days(_fatigued(start), day, day + timedelta(days=1), plan, auckland)
    shifted, _ = project_planned_days(
        _fatigued(datetime(2026, 1, 12, 0, 0)), day, day + timedelta(days=1), plan, UNKNOWN)
    assert [d.mean_fatigue for d in local] == [d.mean_fatigue for d in shifted] == [28.67, 23.19]
