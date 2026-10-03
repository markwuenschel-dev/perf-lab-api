"""F1 — telemetry runs in its own session and never touches the request's transaction.

`best_effort_write` used to commit and roll back the CALLER's session: a shadow write could
commit the request's staged work, or roll it back and expire its ORM objects. The contract
this file holds, one test group per acceptance criterion:

1. **Snapshot completeness** — every telemetry writer takes plain data (schemas, dataclasses,
   scalars, JSON), never an ORM row bound to the request's session.
2. **Independent commit/rollback ownership** — telemetry commits or rolls back only its own
   session: a success commits nothing of the caller's; a failure rolls back nothing of it.
3. **The request's session stays usable** after a telemetry failure — staged work intact,
   ORM objects not expired, further statements succeed.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import typing
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import NoneType, UnionType
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.athlete_state import AthleteState
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.services import (
    capacity_floor_shadow_service,
    decision_telemetry,
    dose_model_shadow_service,
    dose_routing_shadow_service,
    ekf_shadow_service,
    mpc_shadow_service,
    personalization_shadow_service,
    recovery_shadow_service,
)
from app.services.telemetry_common import best_effort_write

WRITERS: list[Callable[..., Any]] = [
    recovery_shadow_service.record_recovery_shadow,
    personalization_shadow_service.record_personalization_shadow,
    capacity_floor_shadow_service.record_floor_candidate,
    decision_telemetry.persist_prescription_decision,
    dose_model_shadow_service.record_dose_model_shadow,
    dose_routing_shadow_service.record_dose_routing,
    ekf_shadow_service.record_ekf_predict,
    ekf_shadow_service.record_ekf_update,
    ekf_shadow_service.record_ekf_wellness_observation,
    mpc_shadow_service.record_mpc_shadow,
]


# ── 1. snapshot completeness ──────────────────────────────────────────────────────────
#
# What this proves, precisely: every parameter of every registered writer, followed
# RECURSIVELY through containers, unions, dataclass fields and Pydantic model fields, is plain
# data — no ORM class anywhere, and ``Any`` only as the value type of a ``str``-keyed dict or
# Mapping (a declared JSON payload; the values of a JSON payload are not type-checked here).
# It is a static proof about declared types; it does not inspect runtime values.

_LEAVES = (int, float, str, bool, bytes, NoneType, datetime, date, timedelta)
_JSON_MAPPINGS = (dict, Mapping)


def _plain_data_violations(hint: Any, path: str, seen: set[Any]) -> list[str]:
    """Every place under `hint` that is not plain data, as dotted paths."""
    if hint is Any:
        return [f"{path}: Any"]
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Literal:
        return []
    if origin is typing.Annotated:  # e.g. a Pydantic discriminated union
        return _plain_data_violations(args[0], path, seen)
    if origin in (typing.Union, UnionType):
        return [v for a in args for v in _plain_data_violations(a, path, seen)]
    if origin is not None:
        container = origin if isinstance(origin, type) else None
        if container is not None and issubclass(container, _JSON_MAPPINGS) and len(args) == 2:
            key, value = args
            out = _plain_data_violations(key, f"{path}[key]", seen)
            if not (key is str and value is Any):  # a declared JSON object: values unchecked
                out += _plain_data_violations(value, f"{path}[value]", seen)
            return out
        if container is not None and issubclass(container, (Sequence, set, frozenset, tuple)):
            return [v for a in args if a is not Ellipsis for v in _plain_data_violations(a, f"{path}[]", seen)]
        return [f"{path}: unsupported generic {hint!r}"]
    if not isinstance(hint, type):
        return [f"{path}: unsupported annotation {hint!r}"]
    if issubclass(hint, Base):
        return [f"{path}: ORM class {hint.__name__}"]
    if issubclass(hint, _LEAVES) or issubclass(hint, enum.Enum):
        return []
    if hint in seen:
        return []
    seen.add(hint)
    if issubclass(hint, BaseModel):
        return [
            v
            for name, field in hint.model_fields.items()
            for v in _plain_data_violations(field.annotation, f"{path}.{name}", seen)
        ]
    if dataclasses.is_dataclass(hint):
        hints = typing.get_type_hints(hint)
        return [
            v for f in dataclasses.fields(hint) for v in _plain_data_violations(hints[f.name], f"{path}.{f.name}", seen)
        ]
    return [f"{path}: not a declared plain-data type ({hint.__name__})"]


def _functions_calling_best_effort_write() -> set[str]:
    """`module:function` for every function in app/ whose body calls best_effort_write."""
    root = Path(__file__).resolve().parents[1] / "app"
    found: set[str] = set()
    for path in root.rglob("*.py"):
        if path.name == "telemetry_common.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = ".".join(path.relative_to(root.parent).with_suffix("").parts)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {
                c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", None)
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
            }
            if "best_effort_write" in calls:
                found.add(f"{module}:{node.name}")
    return found


def test_every_best_effort_write_caller_is_registered_here():
    """Guards the guard, per FUNCTION: a new writer in an already-registered module must be
    registered too, or its parameters would go unchecked."""
    registered = {f"{w.__module__}:{w.__name__}" for w in WRITERS}
    assert _functions_calling_best_effort_write() == registered


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.__name__)
def test_writers_take_plain_data_never_orm_rows(writer):
    hints = typing.get_type_hints(writer)
    violations = [
        v
        for name, hint in hints.items()
        if name not in ("db", "return")
        for v in _plain_data_violations(hint, name, set())
    ]
    assert violations == []


def test_the_contract_check_catches_what_it_claims_to():
    """The checker itself, on known-bad shapes: nested ORM, nested Any, an ORM in a snapshot."""
    @dataclasses.dataclass(frozen=True)
    class LeakySnapshot:
        row: User

    assert _plain_data_violations(list[User], "x", set())
    assert _plain_data_violations(dict[str, Any], "x", set()) == []  # a declared JSON payload
    assert _plain_data_violations(dict[str, list[Any]], "x", set())  # Any nested deeper: flagged
    assert _plain_data_violations(list[Any], "x", set())
    assert _plain_data_violations(dict[int, Any], "x", set())
    assert _plain_data_violations(LeakySnapshot, "x", set())
    assert _plain_data_violations(Sequence[LeakySnapshot] | None, "x", set())


# ── 2 + 3. ownership and a usable request session ─────────────────────────────────────


async def _count(bind: Any, model: type, **where: Any) -> int:
    """Count rows from an INDEPENDENT session — what is actually committed."""
    async with AsyncSession(bind=bind) as fresh:
        stmt = select(func.count()).select_from(model)
        for col, value in where.items():
            stmt = stmt.where(getattr(model, col) == value)
        return int((await fresh.execute(stmt)).scalar_one())


async def _staged_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.flush()  # in the request's open transaction, not committed
    return user


@pytest.mark.asyncio
async def test_a_telemetry_success_commits_nothing_of_the_caller(async_db):
    staged = await _staged_user(async_db, "f1-staged-success@test.com")
    async with best_effort_write(async_db, "probe") as tx:
        assert tx.db is not async_db  # its own session
        tx.db.add(User(email="f1-telemetry-row@test.com", hashed_password="h", is_active=True))
    assert tx.committed is True
    # The telemetry row is durable; the caller's staged row is NOT committed by it.
    assert await _count(async_db.bind, User, email="f1-telemetry-row@test.com") == 1
    assert await _count(async_db.bind, User, email="f1-staged-success@test.com") == 0
    # And the caller still owns its transaction: rolling it back discards only its own row.
    await async_db.rollback()
    assert await _count(async_db.bind, User, email="f1-telemetry-row@test.com") == 1
    assert inspect(staged).transient or inspect(staged).detached


@pytest.mark.asyncio
async def test_a_telemetry_failure_rolls_back_nothing_of_the_caller(async_db):
    staged = await _staged_user(async_db, "f1-staged-failure@test.com")
    email_before = staged.email
    async with best_effort_write(async_db, "probe") as tx:
        tx.db.add(User(email="f1-doomed@test.com", hashed_password="h", is_active=True))
        await tx.db.flush()
        raise RuntimeError("telemetry bug")
    assert tx.failed is True and tx.committed is False
    assert await _count(async_db.bind, User, email="f1-doomed@test.com") == 0
    # 3. The request's session is untouched and usable: the staged row is still pending in
    # its transaction, its ORM object was not expired, and it can still commit.
    assert inspect(staged).expired is False and staged.email == email_before
    assert (await async_db.execute(select(func.count()).select_from(User))).scalar_one() >= 1
    await async_db.commit()
    assert await _count(async_db.bind, User, email="f1-staged-failure@test.com") == 1


@pytest.mark.asyncio
async def test_a_failing_commit_inside_telemetry_leaves_the_request_usable(async_db):
    """A failure at COMMIT (a constraint violation surfaces there, not at add()) stays inside
    the telemetry session."""
    committed = User(email="f1-dup@test.com", hashed_password="h", is_active=True)
    async_db.add(committed)
    await async_db.commit()
    staged = await _staged_user(async_db, "f1-staged-dup@test.com")
    async with best_effort_write(async_db, "probe") as tx:
        tx.db.add(User(email="f1-dup@test.com", hashed_password="h", is_active=True))  # unique
    assert tx.failed is True
    assert inspect(staged).expired is False
    await async_db.commit()
    assert await _count(async_db.bind, User, email="f1-staged-dup@test.com") == 1


@pytest.mark.asyncio
async def test_a_real_writer_runs_beside_an_open_request_transaction(async_db):
    """End to end through a real writer: the shadow row commits on its own; the caller's
    staged work stays pending and uncommitted."""
    user = User(email="f1-recovery@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    staged = await _staged_user(async_db, "f1-staged-recovery@test.com")
    snapshot = WellnessTelemetrySnapshot(
        sleep_hours=7.5, hrv_ms=60.0, resting_hr=50.0, soreness=None, mood=None
    )
    await recovery_shadow_service.record_recovery_shadow(async_db, user.id, snapshot)
    assert await _count(async_db.bind, RecoveryShadowLog, user_id=user.id) == 1
    assert await _count(async_db.bind, User, email="f1-staged-recovery@test.com") == 0
    assert inspect(staged).expired is False


# ── /simulate/projection writes nothing ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simulate_projection_for_a_new_athlete_writes_no_state(async_db):
    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user
    from app.core.db import get_db
    from app.main import app

    user = User(email="f1-simulate@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)

    async def _db():
        yield async_db

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/v1/simulate/projection",
                json={"goal": "Strength", "weeks": 4, "weekly_volume": 60,
                      "intensity": "balanced", "recovery": "standard"},
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert await _count(async_db.bind, AthleteState, user_id=user.id) == 0


# ── a request-session rollback before the shadow must not 500 a committed workout ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_link", [True, False], ids=["explicit-link", "implicit-match"])
async def test_linked_workout_survives_a_failed_kpi_recompute(async_db, monkeypatch, explicit_link):
    """Review repro: an e1RM top set commits an observation, then a failing KPI recompute rolls
    the REQUEST session back (caught, by design) — expiring `planned_session`. The dose-model
    shadow then read `planned_session.prescribed_content` and raised MissingGreenlet outside
    any guard: HTTP 500 on a committed workout, and no shadow row. The linked prescription is
    now captured before the primary commit."""
    from datetime import UTC, datetime, timedelta

    from httpx import ASGITransport, AsyncClient

    from app.core.auth import get_current_user
    from app.core.db import get_db
    from app.main import app
    from app.models.benchmark_definition import BenchmarkDefinition
    from app.models.benchmark_observation import BenchmarkObservation
    from app.models.dose_model_shadow import DoseModelShadowLog
    from app.models.exercise import Exercise
    from app.models.mesocycle import BlockGoal, BlockStatus, MesocycleBlock, PlannedSession
    from app.models.workout_log import WorkoutLog as WorkoutLogORM
    from app.services import dashboard_service

    email = f"f1-linked-{'explicit' if explicit_link else 'implicit'}@test.com"
    user = User(email=email, hashed_password="h", is_active=True)
    async_db.add(user)
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
    await async_db.refresh(user)
    now = datetime.now(UTC).replace(microsecond=0)
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=now.date(), deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)
    session = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=now.date(), week_number=1,
        day_of_week=now.isoweekday(), category="Heavy Lower", modality="Strength",
        domain="strength",
        prescribed_content={"type": "Strength", "duration_min": 45, "exercises": []},
    )
    async_db.add(session)
    await async_db.commit()
    await async_db.refresh(session)

    injected: list[int] = []

    async def _failing_recompute(db, *_a, **_k):
        # Real DB work first: it begins a transaction, so the caller's `db.rollback()` is a
        # REAL rollback that expires the session's objects. A failure raised before any SQL
        # makes that rollback a no-op, and this test would pass with the bug present.
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
        injected.append(1)
        raise RuntimeError("injected KPI recompute failure")

    monkeypatch.setattr(dashboard_service, "recompute_derived_metrics", _failing_recompute)

    payload = {
        "timestamp": (now - timedelta(minutes=5)).isoformat(),
        "modality": "Strength", "duration_minutes": 45.0, "session_rpe": 9.0,
        "sets": [{"exercise_name": "Back Squat", "sets": 1, "load_kg": 150.0, "reps": 1, "rpe": 9.5}],
    }
    if explicit_link:
        payload["planned_session_id"] = session.id
    # The route's rollback expires every object in this shared session — the test's too.
    uid = user.id

    async def _db():
        yield async_db

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/v1/log-workout", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert injected, "fixture must reach the failing KPI recompute (via e1RM extraction)"
    assert resp.status_code == 200, resp.text
    assert await _count(async_db.bind, WorkoutLogORM, user_id=uid) == 1
    assert await _count(async_db.bind, BenchmarkObservation, user_id=uid) >= 1
    assert await _count(async_db.bind, DoseModelShadowLog, user_id=uid) == 1
