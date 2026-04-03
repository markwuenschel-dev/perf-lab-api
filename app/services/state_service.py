from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    capacity_from_legacy,
    sync_legacy_from_vectors,
    unified_from_athlete_row,
)
from app.models.athlete_state import AthleteState
from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import WorkoutLog
from app.logic.dose_engine import calculate_stress_dose
from app.logic.state_update import update_athlete_state


async def initialize_athlete_state(db: AsyncSession, user_id: int) -> UnifiedStateVector:
    """
    Creates baseline S0 if none exists (intermediate athlete defaults).
    """
    x = capacity_from_legacy(300.0, 1000.0, 100.0, 15000.0)
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(x, f, t)

    u = UnifiedStateVector(
        timestamp=datetime.utcnow(),
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={"squat": 0.5, "deadlift": 0.5},
        **leg,
    )

    kwargs = athlete_state_kwargs_from_unified(u)
    row = AthleteState(user_id=user_id, **kwargs)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return unified_from_athlete_row(row)


async def process_new_workout(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
) -> UnifiedStateVector:
    """
    Fetch S(t), compute D(t), evolve to S(t+1), persist.
    """
    result = await db.execute(
        select(AthleteState)
        .filter(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        current_state = await initialize_athlete_state(db, user_id)
    else:
        current_state = unified_from_athlete_row(last_record)

    dose = calculate_stress_dose(log)

    if log.timestamp < current_state.timestamp:
        dt = timedelta(seconds=0)
    else:
        dt = log.timestamp - current_state.timestamp

    new_state_schema = update_athlete_state(current_state, dose, dt, log)

    kwargs = athlete_state_kwargs_from_unified(new_state_schema)
    new_db_record = AthleteState(user_id=user_id, **kwargs)
    db.add(new_db_record)
    await db.commit()
    await db.refresh(new_db_record)

    return unified_from_athlete_row(new_db_record)
