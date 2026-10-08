"""P3a: a workout before the current state head is recorded without a state update, and
live submissions take their time from the server.

Pinned here, through ``POST /v1/log-workout`` wherever the contract is the API:

* applied vs record-only, persisted on the workout and visible after reload (GET /v1/workouts);
* a record-only workout keeps its sets' strength evidence and its planned-session link, and
  writes no state row, no EKF predict and no dose-model shadow;
* ``timestamp_mode``: a slow or fast device submitting live is resolved to server time; a
  deliberate five-minute backdate sent as an event time is kept exactly;
* a future event time is a 422; a head stamped in the server's future gets its own reason
  and is never moved;
* the server time is read AFTER waiting for the chain lock (two real connections);
* the initial-baseline re-anchor still applies a first workout of any date;
* same-day planned-session matching uses the payload's own calendar day, not the server's.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.dose_model_shadow import DoseModelShadowLog
from app.models.ekf_shadow import EkfShadowLog
from app.models.exercise import Exercise
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.workouts import WorkoutLog
from app.services import state_service
from app.services.state_chain_lock import lock_athlete_chain


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _user(db: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_head(db: AsyncSession, user_id: int, head_ts: datetime) -> int:
    """Two state rows (so the first-workout re-anchor does not apply): a baseline two days
    before ``head_ts`` and the head at ``head_ts``. Returns the head's id."""
    for ts in (head_ts - timedelta(days=2), head_ts):
        _, row = state_service._build_baseline_vector(user_id)
        row.timestamp = ts
        db.add(row)
        await db.commit()
    return (await db.execute(
        select(AthleteState.id).where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()


async def _state_count(db: AsyncSession, user_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )).scalar_one()


def _body(ts: datetime, **extra) -> dict:
    return {
        "timestamp": ts.isoformat() if ts.tzinfo else ts.replace(tzinfo=UTC).isoformat(),
        "modality": "Running", "duration_minutes": 30.0, "session_rpe": 6.0, **extra,
    }


async def _client(db: AsyncSession, user: User) -> AsyncIterator[AsyncClient]:
    async def _override_db():
        yield db

    async def _override_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def _post(db: AsyncSession, user: User, body: dict):
    async for c in _client(db, user):
        return await c.post("/v1/log-workout", json=body)
    raise AssertionError("unreachable")


async def _row(db: AsyncSession, workout_log_id: int) -> WorkoutLogORM:
    row = await db.get(WorkoutLogORM, workout_log_id)
    assert row is not None
    await db.refresh(row)
    return row


# ── applied vs record-only ────────────────────────────────────────────────────────────


async def test_a_workout_after_the_head_is_applied_and_linked_to_its_state_row(async_db):
    user = await _user(async_db, "p3a-applied@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=3))
    before = await _state_count(async_db, user.id)
    ts = _now() - timedelta(hours=1)

    resp = await _post(async_db, user, _body(ts))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state_disposition"], body["state_disposition_reason"], body["timestamp_basis"]) == (
        "applied", None, "event_time",
    )
    assert await _state_count(async_db, user.id) == before + 1
    source = (await async_db.execute(
        select(AthleteState.source_workout_log_id).where(AthleteState.user_id == user.id)
        .order_by(AthleteState.id.desc()).limit(1)
    )).scalar_one()
    assert source == body["workout_log_id"]
    row = await _row(async_db, body["workout_log_id"])
    assert (row.state_disposition, row.session_timestamp, row.client_timestamp) == ("applied", ts, ts)


async def test_a_workout_before_the_head_is_record_only_and_says_so_after_reload(async_db):
    user = await _user(async_db, "p3a-record-only@test.com")
    head_id = await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    before = await _state_count(async_db, user.id)

    resp = await _post(async_db, user, _body(_now() - timedelta(days=2)))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state_disposition"], body["state_disposition_reason"]) == (
        "record_only", "event_before_current_state",
    )
    assert await _state_count(async_db, user.id) == before  # no state row
    head = await async_db.get(AthleteState, head_id)
    assert head is not None and body["timestamp"] == head.timestamp.isoformat()  # head returned unchanged
    async for c in _client(async_db, user):
        listed = (await c.get("/v1/workouts")).json()
    assert [(w["id"], w["state_disposition"], w["state_disposition_reason"]) for w in listed] == [
        (body["workout_log_id"], "record_only", "event_before_current_state"),
    ]


async def test_record_only_keeps_evidence_and_the_planned_link_but_writes_no_state_shadows(async_db):
    user = await _user(async_db, "p3a-side-paths@test.com")
    async_db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
    async_db.add(BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
        unit="kg", better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    ))
    await async_db.commit()
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    day = (_now() - timedelta(days=2)).date()
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=day, deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    async_db.add(block)
    await async_db.commit()
    session = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=day, week_number=1,
        day_of_week=day.isoweekday(), category="Heavy Lower", modality="Strength",
        domain="strength", status=SessionStatus.PENDING,
    )
    async_db.add(session)
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    resp = await _post(async_db, user, _body(
        _now() - timedelta(days=2), modality="Strength", planned_session_id=session.id,
        sets=[{"exercise_name": "Back Squat", "sets": 3, "load_kg": 100.0, "reps": 5, "rpe": 9.0}],
    ))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state_disposition"] == "record_only"
    assert await _state_count(async_db, user.id) == before
    await async_db.refresh(session)
    assert (session.status, session.workout_log_id) == (SessionStatus.COMPLETED, body["workout_log_id"])
    evidence = (await async_db.execute(
        select(func.count()).select_from(BenchmarkObservation)
        .where(BenchmarkObservation.user_id == user.id, BenchmarkObservation.source == "workout_extraction")
    )).scalar_one()
    assert evidence == 1  # kept: it can still inform prescribed loads
    ekf = (await async_db.execute(
        select(func.count()).select_from(EkfShadowLog).where(EkfShadowLog.user_id == user.id)
    )).scalar_one()
    dose_model = (await async_db.execute(
        select(func.count()).select_from(DoseModelShadowLog)
        .where(DoseModelShadowLog.workout_log_id == body["workout_log_id"])
    )).scalar_one()
    assert (ekf, dose_model) == (0, 0)


# ── timestamp_mode ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("device_offset", [timedelta(minutes=-5), timedelta(hours=1)], ids=["slow", "fast"])
async def test_a_live_submission_takes_server_time_whatever_the_device_clock(async_db, device_offset):
    user = await _user(async_db, f"p3a-live-{device_offset.total_seconds():.0f}@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(seconds=30))
    device_ts = _now() + device_offset
    t0 = _now()

    resp = await _post(async_db, user, _body(device_ts, timestamp_mode="server_now"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state_disposition"], body["timestamp_basis"]) == ("applied", "server_now")
    row = await _row(async_db, body["workout_log_id"])
    assert t0 <= row.session_timestamp <= _now()
    assert row.client_timestamp == device_ts  # the raw device time, kept for audit
    assert row.received_at is not None and t0 <= row.received_at <= row.session_timestamp


async def test_a_deliberate_five_minute_backdate_is_kept_exactly(async_db):
    user = await _user(async_db, "p3a-backdate@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(seconds=30))
    ts = _now() - timedelta(minutes=5)

    resp = await _post(async_db, user, _body(ts))

    body = resp.json()
    row = await _row(async_db, body["workout_log_id"])
    assert (row.session_timestamp, row.timestamp_basis) == (ts, "event_time")
    assert (body["state_disposition"], body["state_disposition_reason"]) == (
        "record_only", "event_before_current_state",
    )


async def test_a_future_event_time_is_refused_and_writes_nothing(async_db):
    user = await _user(async_db, "p3a-future-event@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    before = await _state_count(async_db, user.id)

    resp = await _post(async_db, user, _body(_now() + timedelta(minutes=2)))

    assert resp.status_code == 422 and "future" in resp.text
    logs = (await async_db.execute(
        select(func.count()).select_from(WorkoutLogORM).where(WorkoutLogORM.user_id == user.id)
    )).scalar_one()
    assert (logs, await _state_count(async_db, user.id)) == (0, before)


async def test_a_head_in_the_future_gets_its_own_reason_and_is_not_moved(async_db):
    user = await _user(async_db, "p3a-future-head@test.com")
    future = _now() + timedelta(hours=1)
    head_id = await _seed_head(async_db, user.id, future)

    resp = await _post(async_db, user, _body(_now(), timestamp_mode="server_now"))

    body = resp.json()
    assert (body["state_disposition"], body["state_disposition_reason"]) == (
        "record_only", "current_state_in_future",
    )
    head = await async_db.get(AthleteState, head_id)
    assert head is not None
    await async_db.refresh(head)
    assert head.timestamp == future


# ── the lock wait (two real connections) ──────────────────────────────────────────────


async def test_server_time_is_read_after_waiting_for_an_earlier_writer(async_db):
    """A benchmark holds the chain lock and commits a head stamped at server time while the
    live workout waits. The workout's receipt precedes that head; its effective time must
    not, or it would be record-only for having waited."""
    factory = async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)
    user = await _user(async_db, "p3a-lock-wait@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    received_at = datetime.now(UTC)

    async def live_log():
        async with factory() as db:
            return await state_service.process_new_workout(
                db, user.id,
                WorkoutLog(timestamp=received_at, timestamp_mode="server_now", modality="Running",
                           duration_minutes=30.0, session_rpe=6.0),
                received_at=received_at,
            )

    async with factory() as holder:
        await lock_athlete_chain(holder, user.id)
        task = asyncio.create_task(live_log())
        await asyncio.sleep(0.5)
        assert not task.done(), "the workout did not wait for the chain lock"
        _, row = state_service._build_baseline_vector(user.id)
        row.timestamp = _now()  # the benchmark's head, after the workout's receipt
        holder.add(row)
        head_ts = row.timestamp
        await holder.commit()
    result = await asyncio.wait_for(task, timeout=10)

    assert received_at.replace(tzinfo=None) < head_ts
    assert result.state_disposition == "applied"
    assert result.session_timestamp >= head_ts


# ── the initial baseline and same-day matching ────────────────────────────────────────


async def test_a_first_workout_of_any_date_is_applied_by_the_baseline_re_anchor(async_db):
    user = await _user(async_db, "p3a-first@test.com")
    await state_service.initialize_athlete_state(async_db, user.id)  # S0, stamped now
    ts = _now() - timedelta(days=3)

    resp = await _post(async_db, user, _body(ts))

    body = resp.json()
    assert body["state_disposition"] == "applied"
    first = (await async_db.execute(
        select(AthleteState.timestamp).where(AthleteState.user_id == user.id)
        .order_by(AthleteState.timestamp.asc()).limit(1)
    )).scalar_one()
    assert first == ts - timedelta(seconds=1)


async def test_an_unrepresentable_early_workout_time_is_refused_before_any_write(async_db):
    """A first workout re-anchors S0 one second before it; at 0001-01-01 there is no such
    instant, which raised OverflowError (a 500). Found beside the P3-pre benchmark repro."""
    user = await _user(async_db, "p3a-min@test.com")

    resp = await _post(async_db, user, {
        "timestamp": "0001-01-01T00:00:00Z", "modality": "Running", "duration_minutes": 30.0,
        "session_rpe": 6.0,
    })

    assert resp.status_code == 422 and "too early" in resp.text
    logs = (await async_db.execute(
        select(func.count()).select_from(WorkoutLogORM).where(WorkoutLogORM.user_id == user.id)
    )).scalar_one()
    assert (logs, await _state_count(async_db, user.id)) == (0, 0)


def _payload_with_other_calendar_day(instant: datetime) -> datetime:
    """``instant`` (UTC-aware) written in an offset whose calendar day differs from the UTC
    day, so the test discriminates the two whatever the time of day it runs."""
    offset = timedelta(hours=13) if instant.hour >= 12 else timedelta(hours=-12)
    return instant.astimezone(timezone(offset))


@pytest.mark.parametrize("mode", ["event_time", "server_now"])
async def test_same_day_matching_uses_the_payload_calendar_day_not_the_server_day(async_db, mode):
    user = await _user(async_db, f"p3a-midnight-{mode}@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=2))
    payload = _payload_with_other_calendar_day(datetime.now(UTC) - timedelta(minutes=1))
    payload_day: date = payload.replace(tzinfo=None).date()
    utc_day: date = payload.astimezone(UTC).date()
    assert payload_day != utc_day
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=min(payload_day, utc_day), deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    async_db.add(block)
    await async_db.commit()
    ids = {}
    for d in (payload_day, utc_day):
        s = PlannedSession(
            block_id=block.id, user_id=user.id, scheduled_date=d, week_number=1,
            day_of_week=d.isoweekday(), category="Easy Run", modality="Running",
            domain="endurance", status=SessionStatus.PENDING,
        )
        async_db.add(s)
        await async_db.commit()
        ids[d] = s.id

    resp = await _post(async_db, user, _body(payload, timestamp_mode=mode))

    assert resp.status_code == 200, resp.text
    row = await _row(async_db, resp.json()["workout_log_id"])
    assert row.planned_session_id == ids[payload_day]


async def test_an_explicit_link_is_unaffected_by_server_time(async_db):
    user = await _user(async_db, "p3a-explicit@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=2))
    yesterday = (_now() - timedelta(days=1)).date()
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=yesterday, deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    async_db.add(block)
    await async_db.commit()
    s = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=yesterday, week_number=1,
        day_of_week=yesterday.isoweekday(), category="Easy Run", modality="Running",
        domain="endurance", status=SessionStatus.PENDING,
    )
    async_db.add(s)
    await async_db.commit()

    resp = await _post(async_db, user, _body(_now(), timestamp_mode="server_now", planned_session_id=s.id))

    assert resp.status_code == 200, resp.text
    await async_db.refresh(s)
    assert (s.status, s.workout_log_id) == (SessionStatus.COMPLETED, resp.json()["workout_log_id"])
