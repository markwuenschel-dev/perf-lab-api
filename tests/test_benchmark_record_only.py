"""P3-pre: a benchmark observation dated before the current state head is record-only.

The workout bug P3a fixed, on the benchmark path: the head-derived result used to be stamped
with the observation's past ``observed_at``, so it sorted below the head and its effect was lost.

Pinned here:

* an on-time measurement is applied, links its state row, and says ``applied``;
* a late one keeps the observation, writes no state row, runs no shadow EKF update and no
  decline machine, and says ``record_only`` / ``event_before_current_state``;
* a head stamped in the server's future gets ``current_state_in_future`` and is not moved;
* an observation at exactly the head's time is applied (only strictly earlier is late);
* for an athlete with no state, the baseline staged for the observation is anchored just
  before it, so the observation applies and becomes the head; a pre-existing S0 is never moved;
* a future ``observed_at`` is a 422 through the route and writes nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.ekf_shadow import EkfShadowLog
from app.models.observation_mapping import ObservationMapping
from app.models.strength_decline_candidate import StrengthDeclineCandidate
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service, state_service


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_definition(db) -> None:
    definition = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
        unit="kg", better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=definition.id, target_vector="capacity", target_key="max_strength",
        mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


async def _seed_head(db, user_id: int, head_ts: datetime) -> int:
    """Two rows (baseline two days earlier, head at ``head_ts``). Returns the head's id."""
    for ts in (head_ts - timedelta(days=2), head_ts):
        _, row = state_service._build_baseline_vector(user_id)
        row.timestamp = ts
        db.add(row)
        await db.commit()
    return (await db.execute(
        select(AthleteState.id).where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()


async def _count(db, model, user_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(model).where(model.user_id == user_id)
    )).scalar_one()


def _measurement(observed_at: datetime | None, raw: float = 150.0) -> BenchmarkObservationCreate:
    return BenchmarkObservationCreate(
        benchmark_code="pl_e1rm_squat", raw_value=raw, source="benchmark_test",
        observed_at=observed_at,
    )


async def _only_obs(db, user_id: int) -> BenchmarkObservation:
    rows = (await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
    )).scalars().all()
    assert len(rows) == 1
    await db.refresh(rows[0])
    return rows[0]


async def test_an_on_time_measurement_is_applied_and_links_its_state_row(async_db):
    user = await _user(async_db, "pre-applied@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=3))
    before = await _count(async_db, AthleteState, user.id)

    await benchmark_service.create_observation(async_db, user.id, _measurement(_now() - timedelta(hours=1)))

    obs = await _only_obs(async_db, user.id)
    assert (obs.state_disposition, obs.state_disposition_reason) == ("applied", None)
    assert await _count(async_db, AthleteState, user.id) == before + 1
    newest = (await async_db.execute(
        select(AthleteState.source_observation_id).where(AthleteState.user_id == user.id)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()
    assert newest == obs.id  # it is the new head


async def test_a_late_measurement_is_kept_but_writes_no_state_or_ekf(async_db):
    user = await _user(async_db, "pre-late@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    before = await _count(async_db, AthleteState, user.id)

    read = await benchmark_service.create_observation(
        async_db, user.id, _measurement(_now() - timedelta(days=1))
    )

    obs = await _only_obs(async_db, user.id)
    assert (obs.state_disposition, obs.state_disposition_reason) == (
        "record_only", "event_before_current_state",
    )
    assert read.state_disposition == "record_only"
    assert await _count(async_db, AthleteState, user.id) == before
    assert await _count(async_db, EkfShadowLog, user.id) == 0


async def _with_watermark(db, email: str) -> int:
    """An athlete whose squat watermark (150 kg) comes from a real, applied, on-time
    measurement — the premise the decline machine needs to judge a low reading at all."""
    user = await _user(db, email)
    await _seed_definition(db)
    await _seed_head(db, user.id, _now() - timedelta(hours=6))
    await benchmark_service.create_observation(db, user.id, _measurement(_now() - timedelta(hours=5), raw=150.0))
    return user.id


async def _candidates(db, user_id: int) -> list[tuple[str, int]]:
    rows = (await db.execute(
        select(StrengthDeclineCandidate.status, StrengthDeclineCandidate.trigger_observation_id)
        .where(StrengthDeclineCandidate.user_id == user_id).order_by(StrengthDeclineCandidate.id)
    )).all()
    return [tuple(r) for r in rows]


async def test_an_on_time_low_reading_opens_a_candidate_control(async_db):
    """Control: with a real watermark, an ON-TIME low reading reaches the decline machine and
    opens a candidate. Without this, the late test below could pass for the wrong reason."""
    uid = await _with_watermark(async_db, "pre-decline-control@test.com")

    await benchmark_service.create_observation(async_db, uid, _measurement(_now() - timedelta(hours=1), raw=45.0))

    assert len(await _candidates(async_db, uid)) == 1


async def test_a_late_low_reading_opens_no_candidate(async_db):
    uid = await _with_watermark(async_db, "pre-decline-late@test.com")

    read = await benchmark_service.create_observation(
        async_db, uid, _measurement(_now() - timedelta(days=1), raw=45.0)
    )

    assert read.state_disposition == "record_only"
    assert await _candidates(async_db, uid) == []


async def test_a_late_low_reading_leaves_an_existing_candidate_untouched(async_db):
    uid = await _with_watermark(async_db, "pre-decline-preserve@test.com")
    await benchmark_service.create_observation(async_db, uid, _measurement(_now() - timedelta(hours=4), raw=45.0))
    opened = await _candidates(async_db, uid)
    assert len(opened) == 1

    # A late, independent low reading: had it reached the machine it could confirm or update.
    await benchmark_service.create_observation(async_db, uid, _measurement(_now() - timedelta(days=20), raw=44.0))

    assert await _candidates(async_db, uid) == opened


async def test_a_head_in_the_future_gets_its_own_reason_and_is_not_moved(async_db):
    user = await _user(async_db, "pre-future-head@test.com")
    await _seed_definition(async_db)
    future = _now() + timedelta(hours=1)
    head_id = await _seed_head(async_db, user.id, future)

    await benchmark_service.create_observation(async_db, user.id, _measurement(None))  # server now

    obs = await _only_obs(async_db, user.id)
    assert (obs.state_disposition, obs.state_disposition_reason) == (
        "record_only", "current_state_in_future",
    )
    head = await async_db.get(AthleteState, head_id)
    assert head is not None
    await async_db.refresh(head)
    assert head.timestamp == future


async def test_an_observation_at_exactly_the_head_time_is_applied(async_db):
    user = await _user(async_db, "pre-tie@test.com")
    await _seed_definition(async_db)
    head_ts = _now() - timedelta(hours=2)
    await _seed_head(async_db, user.id, head_ts)

    await benchmark_service.create_observation(async_db, user.id, _measurement(head_ts))

    assert (await _only_obs(async_db, user.id)).state_disposition == "applied"


async def test_a_fresh_athlete_baseline_is_anchored_before_a_past_observation(async_db):
    user = await _user(async_db, "pre-fresh@test.com")
    await _seed_definition(async_db)
    observed = _now() - timedelta(days=3)

    await benchmark_service.create_observation(async_db, user.id, _measurement(observed))

    obs = await _only_obs(async_db, user.id)
    assert obs.state_disposition == "applied"
    rows = (await async_db.execute(
        select(AthleteState.timestamp, AthleteState.source_observation_id)
        .where(AthleteState.user_id == user.id).order_by(AthleteState.timestamp)
    )).all()
    assert [tuple(r) for r in rows] == [(observed - timedelta(seconds=1), None), (observed, obs.id)]


async def test_an_existing_baseline_is_never_moved_by_an_earlier_observation(async_db):
    user = await _user(async_db, "pre-s0@test.com")
    await _seed_definition(async_db)
    await state_service.initialize_athlete_state(async_db, user.id)
    s0_ts = (await async_db.execute(
        select(AthleteState.timestamp).where(AthleteState.user_id == user.id)
    )).scalar_one()

    await benchmark_service.create_observation(async_db, user.id, _measurement(s0_ts - timedelta(days=1)))

    assert (await _only_obs(async_db, user.id)).state_disposition == "record_only"
    assert (await async_db.execute(
        select(AthleteState.timestamp).where(AthleteState.user_id == user.id)
    )).scalars().all() == [s0_ts]


async def _client(db, user: User) -> AsyncIterator[AsyncClient]:
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


async def test_a_future_observed_at_is_refused_and_writes_nothing(async_db):
    user = await _user(async_db, "pre-future-obs@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    body = {
        "benchmark_code": "pl_e1rm_squat", "raw_value": 150.0, "source": "benchmark_test",
        "observed_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    async for c in _client(async_db, user):
        resp = await c.post("/v1/benchmarks/observations", json=body)
    assert resp.status_code == 422 and "future" in resp.text
    assert await _count(async_db, BenchmarkObservation, user.id) == 0


async def test_an_unrepresentable_early_time_is_refused_before_any_write(async_db):
    """Review repro: a fresh athlete's baseline is anchored one second before the observation;
    at 0001-01-01 there is no such instant, which raised OverflowError (a 500)."""
    user = await _user(async_db, "pre-min@test.com")
    await _seed_definition(async_db)
    body = {
        "benchmark_code": "pl_e1rm_squat", "raw_value": 150.0, "source": "benchmark_test",
        "observed_at": "0001-01-01T00:00:00Z",
    }
    async for c in _client(async_db, user):
        resp = await c.post("/v1/benchmarks/observations", json=body)
    assert resp.status_code == 422 and "too early" in resp.text
    assert await _count(async_db, BenchmarkObservation, user.id) == 0
    assert await _count(async_db, AthleteState, user.id) == 0


async def test_the_route_reports_the_disposition(async_db):
    user = await _user(async_db, "pre-route@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    body = {
        "benchmark_code": "pl_e1rm_squat", "raw_value": 150.0, "source": "benchmark_test",
        "observed_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    async for c in _client(async_db, user):
        resp = await c.post("/v1/benchmarks/observations", json=body)
    assert resp.status_code == 200, resp.text
    assert (resp.json()["state_disposition"], resp.json()["state_disposition_reason"]) == (
        "record_only", "event_before_current_state",
    )
