"""Week review service (plan B3): window membership, joins, counts, what moved, next week."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.logic import prescription_finalize
from app.models.athlete_state import AthleteState
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.telemetry import SessionFeedback
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.schemas.state import UnifiedStateVector
from app.services.planning_service import block_adherence_signals
from app.services.week_review_service import (
    WeekReviewNotFound,
    build_week_review,
    prescribed_rpe_cap,
    trigger_kind,
)

_asyncio = pytest.mark.asyncio

# Block starts Monday 2026-09-07; week 2 is 09-14 … 09-20.
_START = date(2026, 9, 7)
_TODAY = date(2026, 9, 18)
_W2 = date(2026, 9, 14)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _block(db, user_id: int, **kw: Any) -> MesocycleBlock:
    b = MesocycleBlock(
        user_id=user_id,
        goal=BlockGoal.STRENGTH,
        status=kw.pop("status", BlockStatus.ACTIVE),
        duration_weeks=kw.pop("duration_weeks", 4),
        sessions_per_week=3,
        start_date=kw.pop("start_date", _START),
        weekly_template=[],
        deload_every_n_weeks=kw.pop("deload_every_n_weeks", 4),
        deload_volume_factor=0.6,
        **kw,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


async def _session(
    db,
    user_id: int,
    block: MesocycleBlock,
    on: date,
    *,
    week: int = 2,
    status: SessionStatus = SessionStatus.PENDING,
    **kw: Any,
) -> PlannedSession:
    ps = PlannedSession(
        block_id=block.id,
        user_id=user_id,
        scheduled_date=on,
        week_number=week,
        day_of_week=on.isoweekday(),
        category="Heavy Lower",
        modality="Strength",
        status=status,
        is_deload=kw.pop("is_deload", False),
        is_benchmark=kw.pop("is_benchmark", False),
        **kw,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    return ps


async def _log(db, user_id: int, ps: PlannedSession, rpe: float) -> WorkoutLog:
    wl = WorkoutLog(
        user_id=user_id,
        planned_session_id=ps.id,
        session_timestamp=datetime.combine(ps.scheduled_date, datetime.min.time()),
        modality="strength",
        duration_minutes=60.0,
        session_rpe=rpe,
    )
    db.add(wl)
    await db.flush()
    ps.workout_log_id = wl.id
    await db.commit()
    return wl


async def _feedback(db, ps: PlannedSession, **kw: Any) -> None:
    db.add(SessionFeedback(planned_session_id=ps.id, status=kw.pop("status", "completed"), **kw))
    await db.commit()


async def _state(
    db,
    user_id: int,
    at: datetime,
    *,
    cns: float = 0.0,
    knee: float = 0.0,
    max_strength: float = 100.0,
    strength_variance: float = 0.2,
    aerobic_variance: float = 0.2,
    decodable: bool = True,
) -> AthleteState:
    vec = UnifiedStateVector(
        timestamp=at, c_met_aerobic=500.0, c_nm_force=50.0, c_struct=50.0, b_met_anaerobic=50.0
    )
    vec.fatigue_f.cns = cns
    vec.tissue_t.knee = knee
    vec.capacity_x.max_strength = max_strength
    vec.capacity_confidence.max_strength = strength_variance
    vec.capacity_confidence.aerobic = aerobic_variance
    kwargs = athlete_state_kwargs_from_unified(vec)
    kwargs["timestamp"] = at
    if not decodable:
        kwargs["engine_state"] = None
    row = AthleteState(user_id=user_id, **kwargs)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _baseline_state(db, user_id: int) -> None:
    await _state(db, user_id, datetime(2026, 9, 1, 12))


def _sources(review) -> list[str]:
    return [i.source for i in review.next_week]


# --- pure helpers --------------------------------------------------------------------------


def test_prescribed_rpe_is_max_cap_and_never_guessed() -> None:
    content = {"exercises": [{"rpe_cap": 7}, {"rpe_cap": 8.5}, {"rpe_cap": None}, {}]}
    assert prescribed_rpe_cap(content) == 8.5
    assert prescribed_rpe_cap(None) is None
    assert prescribed_rpe_cap({"exercises": [{"rpe_cap": None}]}) is None
    assert prescribed_rpe_cap({"exercises": "garbage"}) is None


def test_private_trigger_name_is_an_alias_of_the_public_one() -> None:
    assert (
        prescription_finalize._derive_plan_revision_triggers  # pyright: ignore[reportPrivateUsage]
        is prescription_finalize.derive_plan_revision_triggers
    )


def test_trigger_kind_separates_capacity_evidence_from_safety() -> None:
    # Every live trigger axis gets a kind; only the capacity one is a measurement question.
    axes = [rule.axis for rule in prescription_finalize._DRIVER_RULES]  # pyright: ignore[reportPrivateUsage]
    kinds = {axis: trigger_kind(axis) for axis in axes}
    assert kinds.pop("c_met_aerobic") == "assess"
    assert kinds and set(kinds.values()) == {"safety"}


# --- window + joins ------------------------------------------------------------------------


@_asyncio
async def test_sessions_belong_by_scheduled_date_not_week_number(async_db) -> None:
    u = await _user(async_db, "wr-window@test.com")
    await _baseline_state(async_db, u.id)
    block = await _block(async_db, u.id)
    other = await _block(async_db, u.id, status=BlockStatus.COMPLETED)
    stays = await _session(async_db, u.id, block, _W2)
    moved_in = await _session(
        async_db, u.id, block, _W2 + timedelta(days=2), week=3,
        original_scheduled_date=_W2 + timedelta(days=8),
    )
    await _session(  # planned in week 2, moved out into week 3
        async_db, u.id, block, _W2 + timedelta(days=7), week=2,
        original_scheduled_date=_W2 + timedelta(days=1),
    )
    await _session(async_db, u.id, other, _W2)  # same dates, other block

    review = await build_week_review(async_db, u.id, today=_TODAY)

    assert review.available is True
    assert review.window is not None
    assert (review.window.week_number, review.window.start, review.window.end) == (
        2, _W2, _W2 + timedelta(days=6)
    )
    assert review.window.is_current_week is True
    assert [s.planned_session_id for s in review.sessions] == [stays.id, moved_in.id]


@_asyncio
async def test_outer_joins_keep_patch_only_completion_and_unprescribed_sessions(async_db) -> None:
    u = await _user(async_db, "wr-join@test.com")
    await _baseline_state(async_db, u.id)
    block = await _block(async_db, u.id)
    logged = await _session(
        async_db, u.id, block, _W2, status=SessionStatus.COMPLETED,
        prescribed_content={"exercises": [{"rpe_cap": 7.5}, {"rpe_cap": 8.0}]},
    )
    await _log(async_db, u.id, logged, rpe=8.0)
    patched = await _session(
        async_db, u.id, block, _W2 + timedelta(days=1), status=SessionStatus.COMPLETED
    )

    review = await build_week_review(async_db, u.id, today=_TODAY)
    by_id = {s.planned_session_id: s for s in review.sessions}

    assert by_id[logged.id].felt_rpe == 8.0
    assert by_id[logged.id].prescribed_rpe == 8.0
    assert by_id[patched.id].workout_log_id is None
    assert by_id[patched.id].felt_rpe is None
    assert by_id[patched.id].prescribed_rpe is None
    assert by_id[patched.id].feedback_status is None


@_asyncio
async def test_counts_adherence_and_modified_match_the_prescriber_aggregate(async_db) -> None:
    u = await _user(async_db, "wr-counts@test.com")
    await _baseline_state(async_db, u.id)
    block = await _block(async_db, u.id)
    done = await _session(async_db, u.id, block, _W2, status=SessionStatus.COMPLETED)
    modified = await _session(
        async_db, u.id, block, _W2 + timedelta(days=1), status=SessionStatus.COMPLETED
    )
    await _feedback(async_db, modified, status="completed", modified_volume=True)
    skipped = await _session(
        async_db, u.id, block, _W2 + timedelta(days=2), status=SessionStatus.SKIPPED
    )
    # A skipped session's modification flag must not count (ADR-0070: one penalty).
    await _feedback(async_db, skipped, status="modified")
    await _session(async_db, u.id, block, _W2 + timedelta(days=3))  # due, still pending
    await _session(async_db, u.id, block, _W2 + timedelta(days=5))  # not yet due

    review = await build_week_review(async_db, u.id, today=_TODAY)
    c = review.counts
    assert c is not None
    assert (c.planned, c.completed, c.skipped, c.modified, c.pending, c.due) == (5, 2, 1, 1, 2, 4)
    assert c.adherence_pct == 50.0
    assert {s.planned_session_id for s in review.sessions if s.modified} == {modified.id}
    assert done.id in {s.planned_session_id for s in review.sessions}

    signals = await block_adherence_signals(async_db, u.id, block.id)
    assert signals["recent_modifications"] == c.modified
    assert signals["recent_skips"] == c.skipped


# --- what moved ----------------------------------------------------------------------------


@_asyncio
async def test_moved_brackets_the_week_and_excludes_insufficient_axes(async_db) -> None:
    u = await _user(async_db, "wr-moved@test.com")
    await _block(async_db, u.id)
    await _state(async_db, u.id, datetime(2026, 9, 6, 12), cns=6.0)  # before prev-week start
    await _state(async_db, u.id, datetime(2026, 9, 13, 12), cns=12.0, max_strength=100.0)
    await _state(
        async_db, u.id, datetime(2026, 9, 19, 12), cns=24.0, max_strength=104.0,
        aerobic_variance=2.0,
    )
    await _state(async_db, u.id, datetime(2026, 9, 22, 12), cns=60.0)  # after the week

    review = await build_week_review(async_db, u.id, week_number=2, today=_TODAY)
    m = review.moved
    assert m is not None
    assert m.previous_week_start_snapshot_at == datetime(2026, 9, 6, 12)
    assert m.start_snapshot_at == datetime(2026, 9, 13, 12)
    assert m.end_snapshot_at == datetime(2026, 9, 19, 12)
    assert (m.mean_fatigue_previous_week_start, m.mean_fatigue_start, m.mean_fatigue_end) == (
        1.0, 2.0, 4.0
    )
    axes = {a.axis: a for a in m.capacity}
    assert axes["max_strength"].measured is True
    assert axes["max_strength"].delta == pytest.approx(4.0)
    aerobic = axes["aerobic"]
    assert aerobic.measured is False
    assert aerobic.status_end == "insufficient"
    assert (aerobic.start, aerobic.end, aerobic.delta) == (None, None, None)


# --- next week -----------------------------------------------------------------------------


@_asyncio
async def test_next_week_deload_and_benchmark_fire_only_when_scheduled(async_db) -> None:
    u = await _user(async_db, "wr-next@test.com")
    await _baseline_state(async_db, u.id)
    block = await _block(async_db, u.id, duration_weeks=6)
    await _session(async_db, u.id, block, _W2 + timedelta(days=7), week=3, is_deload=True)
    await _session(
        async_db, u.id, block, _W2 + timedelta(days=11), week=3, is_benchmark=True,
        benchmark_key="periodic_retest",
    )

    week2 = await build_week_review(async_db, u.id, week_number=2, today=_TODAY)
    assert "block:deload_week" in _sources(week2)
    assert "block:benchmark_session" in _sources(week2)
    assert {i.kind for i in week2.next_week} == {"plan", "assess"}
    assert week2.next_week_status == "changes_listed"

    week1 = await build_week_review(async_db, u.id, week_number=1, today=_TODAY)
    assert week1.next_week == []
    assert week1.next_week_status == "nothing_scheduled_to_change"


@_asyncio
async def test_next_week_block_end(async_db) -> None:
    u = await _user(async_db, "wr-end@test.com")
    await _baseline_state(async_db, u.id)
    await _block(async_db, u.id, duration_weeks=3)

    last = await build_week_review(async_db, u.id, week_number=3, today=_TODAY)
    penultimate = await build_week_review(async_db, u.id, week_number=2, today=_TODAY)
    assert [(i.source, i.title) for i in last.next_week] == [("block:ends", "Block ends")]
    assert [(i.source, i.title) for i in penultimate.next_week] == [
        ("block:ends", "Final week of the block")
    ]


@_asyncio
async def test_lighter_bias_counts_modifications_at_half_weight(async_db) -> None:
    u = await _user(async_db, "wr-bias@test.com")
    await _baseline_state(async_db, u.id)
    block = await _block(async_db, u.id)
    await _session(async_db, u.id, block, _W2, status=SessionStatus.SKIPPED)
    m1 = await _session(
        async_db, u.id, block, _W2 + timedelta(days=1), status=SessionStatus.COMPLETED
    )
    await _feedback(async_db, m1, status="modified")

    # 1 skip + 0.5 × 1 modification = 1.5 < 2
    below = await build_week_review(async_db, u.id, week_number=2, today=_TODAY)
    assert "adherence:lighter_bias" not in _sources(below)

    m2 = await _session(
        async_db, u.id, block, _W2 + timedelta(days=2), status=SessionStatus.COMPLETED
    )
    await _feedback(async_db, m2, modified_intensity=True)

    # 1 skip + 0.5 × 2 modifications = 2.0 ≥ 2
    at = await build_week_review(async_db, u.id, week_number=2, today=_TODAY)
    assert "adherence:lighter_bias" in _sources(at)
    item = next(i for i in at.next_week if i.source == "adherence:lighter_bias")
    assert item.kind == "plan"


@_asyncio
async def test_only_active_triggers_appear_as_safety(async_db) -> None:
    u = await _user(async_db, "wr-trig@test.com")
    await _block(async_db, u.id)
    await _state(async_db, u.id, datetime(2026, 9, 1, 12), knee=50.0)  # approaching, not active

    approaching = await build_week_review(async_db, u.id, week_number=1, today=_TODAY)
    assert not [s for s in _sources(approaching) if s.startswith("trigger:")]

    await _state(async_db, u.id, datetime(2026, 9, 17, 12), knee=70.0)
    active = await build_week_review(async_db, u.id, week_number=1, today=_TODAY)
    triggers = [i for i in active.next_week if i.source.startswith("trigger:")]
    assert [(i.source, i.kind) for i in triggers] == [("trigger:tissue_t.knee", "safety")]


# --- unavailable / not found ---------------------------------------------------------------


@_asyncio
async def test_unavailable_reasons_and_not_found(async_db) -> None:
    u = await _user(async_db, "wr-unavail@test.com")
    assert (await build_week_review(async_db, u.id, today=_TODAY)).reason == "no_active_block"

    block = await _block(async_db, u.id)
    no_state = await build_week_review(async_db, u.id, today=_TODAY)
    assert (no_state.available, no_state.reason) == (False, "no_state")

    await _state(async_db, u.id, datetime(2026, 9, 10, 12), decodable=False)
    invalid = await build_week_review(async_db, u.id, today=_TODAY)
    assert (invalid.available, invalid.reason) == (False, "state_invalid")

    stranger = await _user(async_db, "wr-stranger@test.com")
    with pytest.raises(WeekReviewNotFound):
        await build_week_review(async_db, stranger.id, block_id=block.id, today=_TODAY)
    with pytest.raises(WeekReviewNotFound):
        await build_week_review(async_db, u.id, block_id=block.id, week_number=9, today=_TODAY)
