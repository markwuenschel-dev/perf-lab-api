"""A strength report is saved whole; onboarding is one transaction a retry cannot duplicate.

S2 decision 3 (O1) implementation rulings:

* A strength report's evidence and the profile value derived from it commit together — a failure
  between them leaves neither, including the baseline that evidence seeds for an athlete with no
  state yet.
* Onboarding — profile, self-reported weak points, baseline state, evidence, projection — commits
  once, or not at all.
* Onboarding is retry-safe. The server cannot tell a retry whose first attempt committed (and whose
  response was lost) from the same submission sent twice; either way there is no second baseline,
  report, or weak point. Concurrent duplicates are serialized.

Durability is asserted through sessions that did not make the writes: ``http_client`` shares one
session across requests, so reading back through it would also see uncommitted rows (see
test_observation_durability.py).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from conftest import TEST_DATABASE_URL, _truncate_all
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.onboard import onboard_athlete
from app.logic import onboarding_state as ob
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import AthleteProfile, User
from app.models.weak_point import WeakPoint
from app.schemas.onboarding import OnboardRequest
from app.services import state_service, strength_evidence_service

pytestmark = pytest.mark.asyncio

SQUAT, BENCH, DEADLIFT = "pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"
_LIFTS = {
    SQUAT: ("Back Squat", "squat"),
    BENCH: ("Bench Press", "push_horizontal"),
    DEADLIFT: ("Conventional Deadlift", "hinge"),
}
_NOW = datetime.now(UTC).replace(microsecond=0)


def _ago(days: float) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


_SUBMISSION: dict[str, Any] = {
    "experience_level": "intermediate",
    "goal": "Powerlifting",
    "bodyweight_kg": 82.5,
    "self_reported_weak_points": ["grip", "hip_mobility"],
    "strength": [
        {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(2)},
        {"benchmark_code": BENCH, "method": "rep_set", "load_kg": 80.0, "reps": 3, "rpe": 9.0,
         "performed_at": _ago(1)},
        {"benchmark_code": DEADLIFT, "method": "estimate", "value_kg": 180.0},
    ],
}


async def _catalog(db: AsyncSession) -> None:
    """The canonical lifts, each e1RM definition mapped onto max strength. Without a mapping an
    observation never reaches the state branch, and a state write could not be shown atomic."""
    for code, (name, pattern) in _LIFTS.items():
        db.add(Exercise(
            name=name, modality="Strength", movement_pattern=pattern, load_type="barbell",
            is_benchmark=True, e1rm_benchmark_code=code,
        ))
        definition = BenchmarkDefinition(
            code=code, name=f"{name} e1RM", domain="powerlifting", metric_type="load", unit="kg",
            better_direction="higher", observation_weight=1.0,
            standardization_rules={"floor": 20.0, "cap": 320.0},
        )
        db.add(definition)
        await db.flush()
        db.add(ObservationMapping(
            benchmark_definition_id=definition.id, target_vector="capacity",
            target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
        ))
    await db.commit()


async def _athlete(client, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, user_id


async def _onboard(client, headers: dict[str, str], body: dict[str, Any]):
    return await client.post("/v1/onboard", json=body, headers=headers)


async def _committed(db: AsyncSession, stmt: Any) -> Any:
    """Read through a new session, which sees only committed rows."""
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as fresh:
        return (await fresh.execute(stmt)).scalar_one()


def _count(model: Any, user_id: int) -> Any:
    return select(func.count()).select_from(model).where(model.user_id == user_id)


def _evidence_count(user_id: int, code: str) -> Any:
    return (
        select(func.count())
        .select_from(BenchmarkObservation)
        .join(BenchmarkDefinition, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(BenchmarkObservation.user_id == user_id, BenchmarkDefinition.code == code)
    )


def _profile_value(column: Any, user_id: int) -> Any:
    return select(column).where(AthleteProfile.user_id == user_id)


# ── a strength report ────────────────────────────────────────────────────────────

async def test_a_report_and_the_profile_value_derived_from_it_commit_together(
    http_client, async_db, monkeypatch
):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tx-report@test.com")
    report = {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(2)}

    async def _projection_fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("projection failed")

    monkeypatch.setattr(strength_evidence_service, "_stage_projection", _projection_fails)
    with pytest.raises(RuntimeError, match="projection failed"):
        await http_client.post("/v1/benchmarks/strength-evidence", headers=headers, json=report)

    assert await _committed(async_db, _evidence_count(user_id, SQUAT)) == 0
    # The evidence had already staged a baseline for this stateless athlete; it went too.
    assert await _committed(async_db, _count(AthleteState, user_id)) == 0
    await async_db.rollback()  # what closing the request's session does in production

    # Control: the same report, unobstructed, writes all three — so the zeros above are the
    # transaction boundary, not a path that never wrote.
    monkeypatch.undo()
    saved = await http_client.post("/v1/benchmarks/strength-evidence", headers=headers, json=report)
    assert saved.status_code == 200, saved.text
    assert await _committed(async_db, _evidence_count(user_id, SQUAT)) == 1
    # The baseline the measurement seeded, then the state the measurement produced.
    assert await _committed(async_db, _count(AthleteState, user_id)) == 2
    assert await _committed(async_db, _profile_value(AthleteProfile.squat_1rm, user_id)) == 140.0


# ── onboarding ───────────────────────────────────────────────────────────────────

async def test_onboarding_commits_nothing_when_a_later_step_fails(http_client, async_db, monkeypatch):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tx-onboard-fail@test.com")

    real_stage = strength_evidence_service.stage_strength_report
    calls = 0

    async def _second_report_fails(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second report failed")
        return await real_stage(*args, **kwargs)

    monkeypatch.setattr(strength_evidence_service, "stage_strength_report", _second_report_fails)
    with pytest.raises(RuntimeError, match="second report failed"):
        await _onboard(http_client, headers, _SUBMISSION)
    assert calls == 2

    # Profile basics, weak points, baseline, the first report and its projection: none of it.
    assert await _committed(async_db, _profile_value(AthleteProfile.bodyweight_kg, user_id)) is None
    assert await _committed(async_db, _profile_value(AthleteProfile.squat_1rm, user_id)) is None
    assert await _committed(async_db, _count(WeakPoint, user_id)) == 0
    assert await _committed(async_db, _count(AthleteState, user_id)) == 0
    assert await _committed(async_db, _count(BenchmarkObservation, user_id)) == 0
    await async_db.rollback()


async def test_the_same_onboarding_sent_again_adds_nothing(http_client, async_db):
    """What a client does when the response to a committed onboarding never arrived."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tx-onboard-retry@test.com")

    first = await _onboard(http_client, headers, _SUBMISSION)
    assert first.status_code == 200, first.text
    second = await _onboard(http_client, headers, _SUBMISSION)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()

    assert await _committed(async_db, _count(AthleteState, user_id)) == 1
    for code in (SQUAT, BENCH, DEADLIFT):
        assert await _committed(async_db, _evidence_count(user_id, code)) == 1, code
    assert await _committed(async_db, _count(WeakPoint, user_id)) == 2
    assert await _committed(async_db, _profile_value(AthleteProfile.squat_1rm, user_id)) == 140.0
    assert await _committed(async_db, _profile_value(AthleteProfile.bodyweight_kg, user_id)) == 82.5


async def test_a_changed_resubmission_records_the_new_report_but_never_reseeds(http_client, async_db):
    """A report that differs is a different fact and is recorded; the baseline is not seeded again
    and the profile follows the newest report."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tx-onboard-changed@test.com")

    assert (await _onboard(http_client, headers, _SUBMISSION)).status_code == 200
    changed = {**_SUBMISSION, "strength": [
        {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 145.0, "performed_at": _ago(2)},
    ]}
    resp = await _onboard(http_client, headers, changed)
    assert resp.status_code == 200, resp.text

    assert await _committed(async_db, _count(AthleteState, user_id)) == 1
    assert await _committed(async_db, _evidence_count(user_id, SQUAT)) == 2
    assert await _committed(async_db, _profile_value(AthleteProfile.squat_1rm, user_id)) == 145.0


@pytest_asyncio.fixture(loop_scope="function")
async def engine(_migrated_schema: None):
    """Its own engine, so two onboardings can hold two real connections at once."""
    eng = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    await _truncate_all(eng)
    yield eng
    await eng.dispose()


@pytest.mark.parametrize("profile_before", ["no_profile_row", "basics_already_submitted"])
async def test_concurrent_duplicate_onboardings_write_once(engine, monkeypatch, profile_before):
    """Two identical submissions at once — a double tap, or a client retrying before the first
    finished — write once.

    Both starting points leave nothing else to serialize the two: with no profile row the second
    would fail on the profile's unique user id; with the basics already stored, updating them
    emits no row write for the second to wait on. (When an update does change the profile, that
    row lock alone happens to serialize duplicates — which is why a registered empty shell is not
    a starting point here: it passed with the explicit lock removed.)
    """
    request = OnboardRequest.model_validate(_SUBMISSION)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        await _catalog(db)
        user = User(email="tx-concurrent@test.com", hashed_password="x", is_active=True)
        db.add(user)
        await db.commit()
        user_id = user.id
        if profile_before == "basics_already_submitted":
            db.add(AthleteProfile(
                user_id=user_id,
                primary_goal=request.goal,
                date_of_birth=request.date_of_birth,
                experience_years=request.experience_years,
                experience_level=request.experience_level,
                available_days_per_week=request.available_days_per_week,
                session_duration_minutes=request.session_duration_minutes,
                equipment=request.equipment,
                bodyweight_kg=request.bodyweight_kg,
                run_5k_seconds=request.run_5k_seconds,
                onboarding_status=ob.STATUS_IN_PROGRESS,
            ))
            await db.commit()

    # Hold the first submission inside its transaction long enough that the second is certainly
    # waiting on it; without serialization both would find no state and both would seed.
    real_has_state = state_service.has_state

    async def _slow_has_state(db: AsyncSession, uid: int) -> bool:
        await asyncio.sleep(0.3)
        return await real_has_state(db, uid)

    monkeypatch.setattr(state_service, "has_state", _slow_has_state)

    async def submit() -> Any:
        async with factory() as db:
            current_user = await db.get(User, user_id)
            assert current_user is not None
            return await onboard_athlete(request, db=db, current_user=current_user)

    first, second = await asyncio.gather(submit(), submit())
    assert first == second

    async with factory() as db:
        assert await db.scalar(_count(AthleteState, user_id)) == 1
        for code in (SQUAT, BENCH, DEADLIFT):
            assert await db.scalar(_evidence_count(user_id, code)) == 1, code
        assert await db.scalar(_count(WeakPoint, user_id)) == 2
