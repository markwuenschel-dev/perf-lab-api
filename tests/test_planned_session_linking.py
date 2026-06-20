from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

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
from app.services.state_service import initialize_athlete_state, process_new_workout

pytestmark = pytest.mark.asyncio


async def _create_user(db, email: str = "plan-link@test.com") -> User:
    user = User(email=email, hashed_password="hash", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_log_workout_links_to_pending_planned_session(async_db):
    user = await _create_user(async_db)
    baseline = await initialize_athlete_state(async_db, user.id)
    d = baseline.timestamp.date() + timedelta(days=1)

    block = MesocycleBlock(
        user_id=user.id,
        goal=BlockGoal.STRENGTH,
        status=BlockStatus.ACTIVE,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=d - timedelta(days=1),
        end_date=d + timedelta(days=27),
        weekly_template=[],
        modality_mix={},
    )
    async_db.add(block)
    await async_db.flush()

    ps = PlannedSession(
        block_id=block.id,
        user_id=user.id,
        scheduled_date=d,
        week_number=1,
        day_of_week=d.isoweekday(),
        category="Max Strength",
        modality="Strength",
        status=SessionStatus.PENDING,
    )
    async_db.add(ps)
    await async_db.commit()
    await async_db.refresh(ps)

    log = WorkoutLog(
        timestamp=datetime.combine(d, datetime.min.time(), tzinfo=UTC),
        modality="Strength",
        duration_minutes=60,
        session_rpe=7,
    )
    await process_new_workout(async_db, user.id, log)

    ps_refetched = (
        await async_db.execute(select(PlannedSession).where(PlannedSession.id == ps.id))
    ).scalars().first()
    assert ps_refetched is not None
    assert ps_refetched.status == SessionStatus.COMPLETED
    assert ps_refetched.workout_log_id is not None

    log_row = (
        await async_db.execute(select(WorkoutLogORM).where(WorkoutLogORM.id == ps_refetched.workout_log_id))
    ).scalars().first()
    assert log_row is not None
    assert log_row.planned_session_id == ps.id
