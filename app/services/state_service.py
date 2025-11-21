from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.athlete_state import AthleteState
from app.schemas.workouts import WorkoutLog
from app.schemas.state import UnifiedStateVector
from app.logic.dose_engine import calculate_stress_dose
from app.logic.state_update import update_athlete_state


async def initialize_athlete_state(db: AsyncSession, user_id: int) -> UnifiedStateVector:
    """
    Creates a baseline S0 state if none exists.
    Uses safe defaults for an 'intermediate' athlete.
    """
    s0 = AthleteState(
        user_id=user_id,
        timestamp=datetime.utcnow(),

        # Capacities (Baseline)
        c_met_aerobic=300.0,     # ~Critical Power 300W / decent VO2
        c_nm_force=1000.0,       # Arbitrary force units
        c_struct=100.0,          # Baseline structural integrity
        b_met_anaerobic=15000.0, # W' in Joules

        # Fatigues (Start Fresh)
        f_met_systemic=0.0,
        f_nm_peripheral=0.0,
        f_nm_central=0.0,
        f_struct_damage=0.0,

        # Signals
        s_struct_signal=0.0,

        # Human Factors
        habit_strength=0.5,      # Neutral habit
        skill_state={"squat": 0.5, "deadlift": 0.5},  # Intermediate skill
    )
    db.add(s0)
    await db.commit()
    await db.refresh(s0)
    return UnifiedStateVector.model_validate(s0)


async def process_new_workout(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
) -> UnifiedStateVector:
    """
    Full update loop:
      1. Fetch S(t)  (latest AthleteState)
      2. Compute D(t) from WorkoutLog
      3. Evolve S(t) -> S(t+1)
      4. Persist S(t+1) to athlete_states
    """
    # 1. Fetch S(t)
    result = await db.execute(
        select(AthleteState)
        .filter(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    # Initialize S0 if no history
    if not last_record:
        current_state = await initialize_athlete_state(db, user_id)
    else:
        current_state = UnifiedStateVector.model_validate(last_record)

    # 2. Compute D(t)
    dose = calculate_stress_dose(log)

    # 3. Compute S(t+1)
    if log.timestamp < current_state.timestamp:
        dt = timedelta(seconds=0)
    else:
        dt = log.timestamp - current_state.timestamp

    new_state_schema = update_athlete_state(current_state, dose, dt)

    # 4. Persist S(t+1)
    new_db_record = AthleteState(
        user_id=user_id,
        **new_state_schema.model_dump(exclude={"timestamp"}),
    )
    new_db_record.timestamp = new_state_schema.timestamp

    db.add(new_db_record)
    await db.commit()
    await db.refresh(new_db_record)

    return new_state_schema
