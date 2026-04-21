from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.user import AthleteProfile, User
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.state_service import initialize_athlete_state


pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str = "route-plan@test.com") -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_planning_block_create_list_and_today(async_db):
    user = await _mk_user(async_db)
    profile = AthleteProfile(user_id=user.id, equipment=["dumbbells"])
    async_db.add(profile)
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    async def _override_db():
        yield async_db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            start = date.today().isoformat()
            create_resp = await client.post(
                "/v1/planning/blocks",
                json={
                    "goal": "Strength",
                    "start_date": start,
                    "duration_weeks": 2,
                    "sessions_per_week": 3,
                },
            )
            assert create_resp.status_code == 200, create_resp.text

            list_resp = await client.get("/v1/planning/blocks")
            assert list_resp.status_code == 200
            blocks = list_resp.json()
            assert len(blocks) >= 1

            sessions_resp = await client.get(
                "/v1/planning/sessions",
                params={
                    "start_date": start,
                    "end_date": (date.today() + timedelta(days=14)).isoformat(),
                },
            )
            assert sessions_resp.status_code == 200
            sessions = sessions_resp.json()
            assert len(sessions) >= 1

            today_resp = await client.get(
                "/v1/planning/today",
                params={"goal": TRAINING_GOAL_DEFAULT},
            )
            assert today_resp.status_code == 200
            payload = today_resp.json()
            assert payload["session"] is not None
            assert payload["prescription"] is not None
    finally:
        app.dependency_overrides.clear()
