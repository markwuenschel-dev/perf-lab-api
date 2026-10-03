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

import typing
from collections.abc import Callable
from typing import Any

import pytest
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


def _types_in(hint: Any) -> list[Any]:
    """`hint` and every type nested in it (list[X], X | None, Mapping[str, Y], ...)."""
    out = [hint]
    for arg in typing.get_args(hint):
        out.extend(_types_in(arg))
    return out


def _orm_or_untyped(hint: Any) -> list[str]:
    bad: list[str] = []
    if hint is Any:
        bad.append("Any")
    for t in _types_in(hint):
        if isinstance(t, type) and issubclass(t, Base):
            bad.append(t.__name__)
    return bad


def test_every_writer_is_registered_here():
    """Guards the guard: a new best_effort_write user must be added to WRITERS."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "services"
    users = sorted(
        p.stem
        for p in root.glob("*.py")
        if "best_effort_write(" in p.read_text(encoding="utf-8") and p.stem != "telemetry_common"
    )
    assert users == sorted({w.__module__.rsplit(".", 1)[-1] for w in WRITERS})


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.__name__)
def test_writers_take_snapshots_never_orm_rows(writer):
    hints = typing.get_type_hints(writer)
    offenders = {
        name: bad
        for name, hint in hints.items()
        if name not in ("db", "return") and (bad := _orm_or_untyped(hint))
    }
    assert offenders == {}


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
