"""/planning/today periodization parity (Phase 0, Task 0.C).

`prescribe_for_athlete` (the /next-session path) builds a block_context with
duration_weeks + deload_every_n_weeks, so the periodization envelope
(app/logic/prescriber.py, ADR-0029) fires. The /planning/today endpoint built
an inline block_context that omitted both fields, so weeks_total was always 0
and the envelope was silently skipped on that path. This test drives the real
/planning/today route with a block at duration_weeks=8 and a planned session
at week_number=7 and asserts the envelope annotations show up in `why`.

NOTE: this is an async DB integration test. It runs against the session-scoped
test schema whenever a database is available, and skips only when none is (a hard
failure under REQUIRE_DB in CI).
"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str = "today-periodization@test.com") -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_today_path_applies_periodization_envelope(async_db):
    user = await _mk_user(async_db)
    profile = AthleteProfile(user_id=user.id, equipment=["barbell"])
    async_db.add(profile)
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    block = MesocycleBlock(
        user_id=user.id,
        goal=BlockGoal.STRENGTH,
        duration_weeks=8,
        sessions_per_week=3,
        start_date=date.today(),
        deload_every_n_weeks=4,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)

    session = PlannedSession(
        block_id=block.id,
        user_id=user.id,
        scheduled_date=date.today(),
        week_number=7,
        day_of_week=date.today().isoweekday(),
        category="Heavy Lower",
        modality="Strength",
    )
    async_db.add(session)
    await async_db.commit()

    async def _override_db():
        yield async_db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/v1/planning/today", params={"goal": TRAINING_GOAL_DEFAULT}
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["prescription"] is not None
            why = payload["prescription"]["why"]
            assert why is not None
            constraints = why["constraints_applied"]
            # week 7 of an 8-week block, deload every 4 weeks -> a real
            # periodization phase (not a no-op), with an RPE target annotated.
            assert any(c.startswith("block:phase=") for c in constraints), constraints
            assert any(c.startswith("block:rpe_target=") for c in constraints), constraints
    finally:
        app.dependency_overrides.clear()
