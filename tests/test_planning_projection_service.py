"""C1b planned-week projection service (ADR-0073).

Pure rollout tests drive ``project_planned_days`` with transient ORM objects; the DB tests go
through ``planned_week_projection`` against a real schema to pin session selection (PENDING
only, membership by ``scheduled_date``) and the unavailable reasons.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

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
