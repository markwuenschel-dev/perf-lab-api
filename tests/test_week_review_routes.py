"""GET /v1/planning/week-review — auth, unavailable reasons (200, not 409), shape, 404."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.main import app
from app.models.athlete_state import AthleteState
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.user import User
from app.schemas.state import UnifiedStateVector

pytestmark = pytest.mark.asyncio

URL = "/v1/planning/week-review"


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _state(db, user_id: int, *, decodable: bool = True) -> None:
    at = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    vec = UnifiedStateVector(
        timestamp=at, c_met_aerobic=500.0, c_nm_force=50.0, c_struct=50.0, b_met_anaerobic=50.0
    )
    kwargs = athlete_state_kwargs_from_unified(vec)
    kwargs["timestamp"] = at
    if not decodable:
        kwargs["engine_state"] = None
    db.add(AthleteState(user_id=user_id, **kwargs))
    await db.commit()


async def _block_with_session(db, user_id: int) -> MesocycleBlock:
    today = date.today()
    block = MesocycleBlock(
        user_id=user_id,
        goal=BlockGoal.STRENGTH,
        status=BlockStatus.ACTIVE,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=today,
        weekly_template=[],
    )
    db.add(block)
    await db.commit()
    await db.refresh(block)
    db.add(
        PlannedSession(
            block_id=block.id,
            user_id=user_id,
            scheduled_date=today,
            week_number=1,
            day_of_week=today.isoweekday(),
            category="Heavy Lower",
            modality="Strength",
            status=SessionStatus.PENDING,
        )
    )
    await db.commit()
    return block


async def _client(db, user: User | None) -> AsyncIterator[AsyncClient]:
    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    if user is not None:

        async def _override_user() -> User:
            return user

        app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def test_requires_auth(async_db) -> None:
    async for client in _client(async_db, None):
        resp = await client.get(URL)
        assert resp.status_code == 401


async def test_no_active_block_is_200_unavailable(async_db) -> None:
    user = await _user(async_db, "wr-route-noblock@test.com")
    await _state(async_db, user.id)
    async for client in _client(async_db, user):
        resp = await client.get(URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "no_active_block"


async def test_state_invalid_is_200_unavailable_not_409(async_db) -> None:
    user = await _user(async_db, "wr-route-invalid@test.com")
    await _block_with_session(async_db, user.id)
    await _state(async_db, user.id, decodable=False)
    async for client in _client(async_db, user):
        resp = await client.get(URL)
        assert resp.status_code == 200
        body = resp.json()
        assert (body["available"], body["reason"]) == (False, "state_invalid")
        assert body["window"]["week_number"] == 1


async def test_shape_and_not_found(async_db) -> None:
    user = await _user(async_db, "wr-route-shape@test.com")
    block = await _block_with_session(async_db, user.id)
    await _state(async_db, user.id)
    async for client in _client(async_db, user):
        resp = await client.get(URL)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["window"]["block_id"] == block.id
        assert body["window"]["is_current_week"] is True
        assert len(body["sessions"]) == 1
        session = body["sessions"][0]
        assert session["status"] == "pending"
        assert session["felt_rpe"] is None and session["prescribed_rpe"] is None
        assert body["counts"]["planned"] == 1
        assert body["counts"]["due"] == 1
        assert body["counts"]["adherence_pct"] == 0.0
        assert len(body["moved"]["capacity"]) == 8
        assert body["next_week_status"] in {"changes_listed", "nothing_scheduled_to_change"}

        explicit = await client.get(URL, params={"block_id": block.id, "week_number": 4})
        assert explicit.status_code == 200
        assert explicit.json()["next_week"][0]["source"] == "block:ends"

        assert (await client.get(URL, params={"block_id": block.id + 999})).status_code == 404
        assert (
            await client.get(URL, params={"block_id": block.id, "week_number": 5})
        ).status_code == 404
        assert (await client.get(URL, params={"week_number": 0})).status_code == 422
