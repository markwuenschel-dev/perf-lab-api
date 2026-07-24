"""/next-session and /planning/today resolve the SAME target session.

Regression for the divergence where the two prescribe entry points reassembled
"today's target PlannedSession" with different semantics:

* /planning/today used planning_service.get_today_session — user-wide, PENDING,
  ORDER BY id ASC.
* /next-session (prescribe no-planned-session branch) scoped to the *latest
  ACTIVE block* with NO ORDER BY — so it could target a different session than
  /planning/today, and pick nondeterministically when >1 matched.

Now both resolve through get_today_session, so they target the same row by
construction. This test constructs the exact divergence: a leftover PENDING
session today in a COMPLETED block (lower id) and a PENDING session in the
current ACTIVE block (higher id). The canonical resolver picks the lowest id
(the COMPLETED-block session); the old block-scoped path would have picked the
ACTIVE-block session. We assert /next-session writes its prescription into the
lowest-id session and leaves the active-block session untouched.

Async DB integration test (session-scoped test schema); skips only when no DB.
"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
)
from app.models.user import AthleteProfile, User
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _mk_block(db, user_id: int, status: BlockStatus) -> MesocycleBlock:
    block = MesocycleBlock(
        user_id=user_id,
        goal=BlockGoal.STRENGTH,
        duration_weeks=8,
        sessions_per_week=3,
        start_date=date.today(),
        deload_every_n_weeks=4,
        status=status,
    )
    db.add(block)
    await db.commit()
    await db.refresh(block)
    return block


async def _mk_pending_today(db, user_id: int, block_id: int) -> PlannedSession:
    session = PlannedSession(
        block_id=block_id,
        user_id=user_id,
        scheduled_date=date.today(),
        week_number=1,
        day_of_week=date.today().isoweekday(),
        category="Heavy Lower",
        modality="Strength",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def test_next_session_targets_the_user_wide_lowest_id_pending_session(async_db):
    user = User(email="today-resolver@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    # Lower id: a leftover PENDING session today in a COMPLETED block.
    old_block = await _mk_block(async_db, user.id, BlockStatus.COMPLETED)
    leftover = await _mk_pending_today(async_db, user.id, old_block.id)
    # Higher id: a PENDING session today in the current ACTIVE block — what the
    # old block-scoped /next-session path would have picked.
    active_block = await _mk_block(async_db, user.id, BlockStatus.ACTIVE)
    active_session = await _mk_pending_today(async_db, user.id, active_block.id)

    assert leftover.id < active_session.id  # ordering the fix depends on

    async def _override_db():
        yield async_db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/v1/next-session", params={"goal": TRAINING_GOAL_DEFAULT}
            )
            assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()

    await async_db.refresh(leftover)
    await async_db.refresh(active_session)
    # The canonical resolver picks the lowest-id session; /next-session persisted
    # into it, not into the active-block session the old path would have chosen.
    assert leftover.prescribed_content is not None
    assert active_session.prescribed_content is None
