"""Shared fixtures for the replay tests: athletes with a seeded baseline, events that arrive in a
chosen order through the real writers, and an oracle that receives the same events chronologically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.logic.tail_replay import row_columns
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import WorkoutLog
from app.services import benchmark_service, state_service
from app.services.tail_replay_service import NewEventRef


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


BASE = utc_now() - timedelta(hours=60)  # events land between BASE and BASE + 40 h: all in the past


@dataclass
class Ev:
    kind: str
    hours: float
    raw: float = 0.0
    extra: dict[str, Any] | None = None

    @property
    def ts(self) -> datetime:
        return BASE + timedelta(hours=self.hours)


def W(hours: float, **extra: Any) -> Ev:
    return Ev("workout", hours, extra={"modality": "Strength", "duration_minutes": 60.0,
                                       "session_rpe": 8.0, **extra})


def B(hours: float, raw: float = 55.0) -> Ev:
    return Ev("benchmark", hours, raw=raw)


async def make_user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def seed_definitions(db) -> None:
    if (await db.execute(
        select(BenchmarkDefinition.id).where(BenchmarkDefinition.code == "aero_test")
    )).first() is not None:
        return
    aero = BenchmarkDefinition(
        code="aero_test", name="Aerobic test", domain="endurance", metric_type="load", unit="u",
        better_direction="higher", observation_weight=0.8,
        standardization_rules={"floor": 10.0, "cap": 100.0},
    )
    strength = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
        unit="kg", better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add_all([aero, strength])
    await db.flush()
    db.add_all([
        ObservationMapping(
            benchmark_definition_id=aero.id, target_vector="capacity", target_key="aerobic",
            mapping_type="residual", coefficient=1.0, intercept=0.0,
        ),
        ObservationMapping(
            benchmark_definition_id=aero.id, target_vector="tissue", target_key="lumbar",
            mapping_type="direct", coefficient=0.5, intercept=20.0, config={"scale": 25.0, "amp": 3.0},
        ),
        ObservationMapping(
            benchmark_definition_id=strength.id, target_vector="capacity", target_key="max_strength",
            mapping_type="residual", coefficient=1.0, intercept=0.0,
        ),
    ])
    await db.commit()


async def seeded_athlete(db, email: str, *, captured: bool = True, seed: bool = True) -> int:
    """An athlete with two baseline rows before BASE (so no first-event re-anchor applies), or
    none at all (``seed=False``: the first event stages the baseline)."""
    uid = (await make_user(db, email)).id
    for ts in (BASE - timedelta(days=2), BASE - timedelta(days=1)) if seed else ():
        _, row = state_service._build_baseline_vector(uid)
        row.timestamp = ts
        if not captured:
            row.event_kind = None
        db.add(row)
        await db.commit()
    return uid


async def arrive_event(db, uid: int, ev: Ev) -> NewEventRef:
    user = await db.get(User, uid)
    assert user is not None
    if ev.kind == "workout":
        await state_service.process_new_workout(
            db, uid, WorkoutLog(timestamp=ev.ts.replace(tzinfo=UTC), **(ev.extra or {})),
            received_at=datetime.now(UTC),
        )
        wid = (await db.execute(
            select(WorkoutLogORM.id).where(WorkoutLogORM.user_id == uid).order_by(WorkoutLogORM.id.desc()).limit(1)
        )).scalar_one()
        return NewEventRef("workout", wid)
    await benchmark_service.create_observation(
        db, uid, BenchmarkObservationCreate(
            benchmark_code="aero_test", raw_value=ev.raw, source="benchmark_test", observed_at=ev.ts,
        ),
    )
    oid = (await db.execute(
        select(BenchmarkObservation.id).where(BenchmarkObservation.user_id == uid)
        .order_by(BenchmarkObservation.id.desc()).limit(1)
    )).scalar_one()
    return NewEventRef("benchmark", oid)


async def head_row(db, uid: int) -> AthleteState:
    row = (await db.execute(
        select(AthleteState).where(AthleteState.user_id == uid)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()
    await db.refresh(row)
    return row


async def is_record_only(db, ref: NewEventRef) -> bool:
    model = WorkoutLogORM if ref.kind == "workout" else BenchmarkObservation
    row = await db.get(model, ref.event_id)
    assert row is not None
    await db.refresh(row)
    return row.state_disposition == "record_only"


async def oracle_head(db, email: str, events: list[Ev]) -> dict[str, Any]:
    """The same events through the live writers, in true chronological order."""
    uid = await seeded_athlete(db, email)
    for ev in sorted(events, key=lambda e: e.hours):  # stable: equal hours keep their given order
        await arrive_event(db, uid, ev)
    return row_columns(await head_row(db, uid))


async def arrive_all(db, email: str, arrivals: list[Ev]) -> tuple[int, list[NewEventRef], list[NewEventRef]]:
    """Events arriving in the given order. Returns (user, late refs in arrival order, all refs)."""
    uid = await seeded_athlete(db, email)
    late: list[NewEventRef] = []
    refs: list[NewEventRef] = []
    for ev in arrivals:
        ref = await arrive_event(db, uid, ev)
        refs.append(ref)
        if await is_record_only(db, ref):
            late.append(ref)
    return uid, late, refs


