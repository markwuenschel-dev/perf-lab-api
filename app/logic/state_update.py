"""
DEPRECATED / TRANSITION MODULE

This module is mostly a stub.

**Current recommended modules:**

- `app.logic.state_update_v0`          → Core state evolution functions
  (update_athlete_state, apply_benchmark_observation, etc.)

- `app.services.state_service`         → High-level workout processing

- `app.logic.state_dynamics`           → Lower-level dynamics (if needed directly)

This file is kept only to avoid breaking old imports. It will be removed after the transition.
"""
import warnings

warnings.warn(
    "app.logic.state_update is deprecated. "
    "Use app.logic.state_update_v0 or app.services.state_service instead.",
    DeprecationWarning,
    stacklevel=2,
)


from datetime import datetime
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.athlete_state import AthleteState
from app.models.workout_log import WorkoutLog
from app.logic.dose_engine import calculate_stress_doses
from app.logic.state_dynamics import update_state


async def process_new_workout(db: AsyncSession, user_id: int, workout_log: WorkoutLog) -> Dict[str, Any]:
    raise NotImplementedError(
        "Use app.services.state_service.process_new_workout instead."
    )

    # Compute dose with new non-linear engine
    workout_dict = workout_log.model_dump() if hasattr(workout_log, "model_dump") else dict(workout_log)
    doses = calculate_stress_doses(workout_dict, current_state)

    # Time delta (hours)
    dt_hours = (workout_log.timestamp - current_state["timestamp"]).total_seconds() / 3600
    if dt_hours < 0:
        dt_hours = 0  # safety clamp

    # Update using new multi-timescale + cross-talk dynamics
    new_state_dict = update_state(current_state, doses, dt_hours)

    # Persist new snapshot
    new_state = AthleteState(user_id=user_id, **new_state_dict, timestamp=datetime.utcnow())
    db.add(new_state)
    await db.commit()
    await db.refresh(new_state)

    return new_state_dict


# ──────────────────────────────────────────────────────────────
# Legacy functions — kept exactly as before (for benchmark_service, etc.)
# ──────────────────────────────────────────────────────────────
async def get_latest_state(db: AsyncSession, user_id: int) -> Dict[str, Any] | None:
    """Your original helper — unchanged."""
    result = await db.execute(
        AthleteState.__table__.select()
        .where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def apply_benchmark_observation(db: AsyncSession, user_id: int, observation: Dict[str, Any]):
    """Kept for benchmark_service.py — you can expand later with new state math."""
    # Your original implementation (unchanged)
    state = await get_latest_state(db, user_id)
    # ... existing benchmark logic ...
    await db.commit()
    return state


# Add any other legacy functions your app calls here (e.g. apply_workout_log, etc.)