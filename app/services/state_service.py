from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    capacity_from_legacy,
    sync_legacy_from_vectors,
    unified_from_athlete_row,
)
from app.engine.phi_table import default_phi_for_row
from app.models.athlete_state import AthleteState
from app.models.exercise import Exercise
from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import WorkoutLog
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import update_athlete_state


_BASELINE_CAPACITIES = {
    "beginner":     dict(c_met_aerobic=180.0,  c_nm_force=500.0,   c_struct=60.0,  b_met_anaerobic=8000.0),
    "intermediate": dict(c_met_aerobic=300.0,  c_nm_force=1000.0,  c_struct=100.0, b_met_anaerobic=15000.0),
    "advanced":     dict(c_met_aerobic=500.0,  c_nm_force=1800.0,  c_struct=160.0, b_met_anaerobic=25000.0),
    "elite":        dict(c_met_aerobic=650.0,  c_nm_force=2500.0,  c_struct=220.0, b_met_anaerobic=35000.0),
}


def _build_baseline_vector(
    user_id: int,
    experience_level: str = "intermediate",
    squat_1rm_kg: float | None = None,
) -> tuple[UnifiedStateVector, AthleteState]:
    """Build S0 and the matching ORM row — does NOT touch the DB."""
    caps = _BASELINE_CAPACITIES.get(experience_level, _BASELINE_CAPACITIES["intermediate"])
    c_nm_force = squat_1rm_kg * 10.0 if squat_1rm_kg is not None else caps["c_nm_force"]
    x = capacity_from_legacy(
        caps["c_met_aerobic"],
        c_nm_force,
        caps["c_struct"],
        caps["b_met_anaerobic"],
    )
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
    row = AthleteState(user_id=user_id, **athlete_state_kwargs_from_unified(u))
    return u, row


async def initialize_athlete_state(
    db: AsyncSession,
    user_id: int,
    *,
    experience_level: str = "intermediate",
    squat_1rm_kg: float | None = None,
) -> UnifiedStateVector:
    """Creates baseline S0 for a new user and commits it."""
    _, row = _build_baseline_vector(user_id, experience_level, squat_1rm_kg)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return unified_from_athlete_row(row)


async def _resolve_exercise_phis(
    db: AsyncSession,
    log: WorkoutLog,
) -> WorkoutLog:
    """
    Populate ExerciseEntry.phi_* fields from Exercise DB rows.

    Entries with an exercise_id are looked up first; entries with only a name
    are matched by name. Entries that already have phi_adapt set are left alone.
    Falls back silently — the dose engine uses modality defaults when phis are absent.
    """
    if not log.exercises:
        return log

    # Collect IDs and names to resolve
    ids = [e.exercise_id for e in log.exercises if e.exercise_id is not None]
    names = [e.exercise_name for e in log.exercises if e.exercise_name and e.exercise_id is None]

    rows_by_id: dict[int, Exercise] = {}
    rows_by_name: dict[str, Exercise] = {}

    if ids:
        res = await db.execute(select(Exercise).where(Exercise.id.in_(ids)))
        for row in res.scalars().all():
            rows_by_id[row.id] = row
    if names:
        res = await db.execute(select(Exercise).where(Exercise.name.in_(names)))
        for row in res.scalars().all():
            rows_by_name[row.name] = row

    # Build a mutable copy of exercises with resolved phis
    resolved_entries = []
    for entry in log.exercises:
        ex_row: Exercise | None = None
        if entry.exercise_id is not None:
            ex_row = rows_by_id.get(entry.exercise_id)
        elif entry.exercise_name:
            ex_row = rows_by_name.get(entry.exercise_name)

        if ex_row is not None and ex_row.phi_adapt:
            # Populate from DB row — build updated entry
            updated = entry.model_copy(
                update={
                    "phi_adapt": dict(ex_row.phi_adapt or {}),
                    "phi_fatigue": dict(ex_row.phi_fatigue or {}),
                    "phi_tissue": dict(ex_row.phi_tissue or {}),
                    "energy_mix": dict(ex_row.energy_mix or {}),
                    "modality": ex_row.modality,
                    "movement_pattern": ex_row.movement_pattern,
                    "skill_demand": ex_row.skill_demand,
                    "impact_level": ex_row.impact_level,
                    "recovery_cost": ex_row.recovery_cost,
                    "weak_point_tags": list(ex_row.weak_point_tags or []),
                    "sport_domains": list(ex_row.sport_domains or []),
                }
            )
            resolved_entries.append(updated)
        elif ex_row is not None and not entry.phi_adapt:
            # DB row exists but has no phi vectors — compute defaults from row metadata
            phi_pack = default_phi_for_row(
                ex_row.modality or log.modality,
                ex_row.movement_pattern or "mixed",
                float(ex_row.skill_demand or 0.5),
                float(ex_row.impact_level or 0.5),
            )
            updated = entry.model_copy(
                update={
                    "phi_adapt": phi_pack["phi_adapt"],
                    "phi_fatigue": phi_pack["phi_fatigue"],
                    "phi_tissue": phi_pack["phi_tissue"],
                    "energy_mix": phi_pack["energy_mix"],
                    "modality": ex_row.modality,
                    "movement_pattern": ex_row.movement_pattern,
                    "skill_demand": ex_row.skill_demand,
                    "impact_level": ex_row.impact_level,
                    "recovery_cost": ex_row.recovery_cost,
                    "weak_point_tags": list(ex_row.weak_point_tags or []),
                    "sport_domains": list(ex_row.sport_domains or []),
                }
            )
            resolved_entries.append(updated)
        else:
            resolved_entries.append(entry)

    return log.model_copy(update={"exercises": resolved_entries})


async def process_new_workout(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
) -> UnifiedStateVector:
    """
    Fetch S(t), resolve exercise phi vectors, compute D(t), evolve to S(t+1), persist.
    """
    result = await db.execute(
        select(AthleteState)
        .filter(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )
    last_record = result.scalars().first()

    if not last_record:
        # Build and stage the baseline row without committing yet — the whole
        # operation (init + first workout) will commit atomically at the end.
        current_state, baseline_row = _build_baseline_vector(user_id)
        db.add(baseline_row)
    else:
        current_state = unified_from_athlete_row(last_record)

    # Resolve exercise phi vectors from DB so dose engine is exercise-aware
    log = await _resolve_exercise_phis(db, log)

    dose = calculate_stress_dose(log)

    # Persist raw workout event for replay/audit and planning linkage.
    workout_row = WorkoutLogORM(
        user_id=user_id,
        planned_session_id=log.planned_session_id,
        session_timestamp=log.timestamp.replace(tzinfo=None) if log.timestamp.tzinfo else log.timestamp,
        modality=log.modality,
        duration_minutes=log.duration_minutes,
        session_rpe=log.session_rpe,
        avg_rir=log.avg_rir,
        distance_meters=log.distance_meters or 0.0,
        total_volume_load=log.total_volume_load or 0.0,
        sleep_quality=log.sleep_quality,
        life_stress_inverse=log.life_stress_inverse,
        dose_snapshot=dose.model_dump(),
        is_benchmark=log.is_benchmark,
        benchmark_results=log.benchmark_results,
    )
    db.add(workout_row)
    await db.flush()

    # If no explicit planned_session_id was provided, best-effort match by date.
    planned_session = None
    if log.planned_session_id is not None:
        ps_result = await db.execute(
            select(PlannedSession).where(
                PlannedSession.id == log.planned_session_id,
                PlannedSession.user_id == user_id,
            )
        )
        planned_session = ps_result.scalars().first()
    else:
        session_day = workout_row.session_timestamp.date()
        ps_result = await db.execute(
            select(PlannedSession)
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.scheduled_date == session_day,
                PlannedSession.status == SessionStatus.PENDING,
            )
            .order_by(PlannedSession.id.asc())
            .limit(1)
        )
        planned_session = ps_result.scalars().first()

    if planned_session is not None:
        planned_session.workout_log_id = workout_row.id
        planned_session.status = SessionStatus.COMPLETED
        planned_session.completed_at = datetime.utcnow()
        workout_row.planned_session_id = planned_session.id

    # Normalize both timestamps to UTC-naive for comparison
    # (DB returns naive datetimes; log.timestamp may be tz-aware)
    log_ts = log.timestamp.replace(tzinfo=None) if log.timestamp.tzinfo else log.timestamp
    state_ts = current_state.timestamp.replace(tzinfo=None) if current_state.timestamp.tzinfo else current_state.timestamp

    if log_ts < state_ts:
        dt = timedelta(seconds=0)
    else:
        dt = log_ts - state_ts

    new_state_schema = update_athlete_state(current_state, dose, dt, log)

    kwargs = athlete_state_kwargs_from_unified(new_state_schema)
    new_db_record = AthleteState(user_id=user_id, **kwargs)
    db.add(new_db_record)
    await db.commit()
    await db.refresh(new_db_record)

    return unified_from_athlete_row(new_db_record)
