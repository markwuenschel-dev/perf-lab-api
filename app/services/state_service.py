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
        current_state = await initialize_athlete_state(db, user_id)
    else:
        current_state = unified_from_athlete_row(last_record)

    # Resolve exercise phi vectors from DB so dose engine is exercise-aware
    log = await _resolve_exercise_phis(db, log)

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
