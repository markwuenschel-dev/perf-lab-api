import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.phi_table import default_phi_for_row
from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    build_unified_state_vector,
    capacity_from_legacy,
    unified_from_athlete_row,
)
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import update_athlete_state
from app.models.athlete_state import AthleteState
from app.models.exercise import Exercise
from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import ExerciseEntry, WorkoutLog

_BASELINE_CAPACITIES = {
    "beginner":     {"c_met_aerobic": 180.0,  "c_nm_force": 500.0,   "c_struct": 60.0,  "b_met_anaerobic": 8000.0},
    "intermediate": {"c_met_aerobic": 300.0,  "c_nm_force": 1000.0,  "c_struct": 100.0, "b_met_anaerobic": 15000.0},
    "advanced":     {"c_met_aerobic": 500.0,  "c_nm_force": 1800.0,  "c_struct": 160.0, "b_met_anaerobic": 25000.0},
    "elite":        {"c_met_aerobic": 650.0,  "c_nm_force": 2500.0,  "c_struct": 220.0, "b_met_anaerobic": 35000.0},
}

# Per-lift skill seed by experience level (0–1). The prescriber triggers a
# Skill-Acquisition session when squat skill < 0.55, so a flat default would
# mask that path — seed from experience instead.
_SKILL_BY_LEVEL = {
    "beginner": 0.35,
    "intermediate": 0.55,
    "advanced": 0.70,
    "elite": 0.85,
}


def _habit_strength_from_experience(experience_years: float) -> float:
    """Seed adherence/habit from training history. Clamped to the schema's 0–1
    bound; an adherence-driven refresh happens later in process_new_workout."""
    return max(0.0, min(0.85, 0.3 + 0.1 * experience_years))


def _baseline_skill_state(
    experience_level: str,
    squat_1rm_kg: float | None,
    deadlift_1rm_kg: float | None,
    bench_1rm_kg: float | None,
) -> dict[str, float]:
    """Seed per-lift skill from experience level, bumped where a 1RM is supplied
    (a known 1RM implies the athlete already performs the pattern)."""
    base = _SKILL_BY_LEVEL.get(experience_level, _SKILL_BY_LEVEL["intermediate"])

    def lift(one_rm: float | None) -> float:
        return round(min(0.95, base + 0.10) if one_rm is not None else base, 3)

    return {
        "squat": lift(squat_1rm_kg),
        "deadlift": lift(deadlift_1rm_kg),
        "bench": lift(bench_1rm_kg),
    }


def _aerobic_from_run_5k(run_5k_seconds: float) -> float:
    """Seed aerobic capacity from a 5K time: faster → higher. Linear between a
    ~15:00 5K (engine aerobic ceiling) and a ~35:00 5K (a modest base), clamped
    to the engine's [180, 650] aerobic range."""
    fast_s, fast_cap = 900.0, 650.0    # ~15:00 → engine aerobic ceiling
    slow_s, slow_cap = 2100.0, 180.0   # ~35:00 → beginner-ish base
    if run_5k_seconds <= fast_s:
        return fast_cap
    if run_5k_seconds >= slow_s:
        return slow_cap
    frac = (run_5k_seconds - fast_s) / (slow_s - fast_s)
    return fast_cap - frac * (fast_cap - slow_cap)


def _build_baseline_vector(
    user_id: int,
    experience_level: str = "intermediate",
    squat_1rm_kg: float | None = None,
    deadlift_1rm_kg: float | None = None,
    bench_1rm_kg: float | None = None,
    bodyweight_kg: float | None = None,
    run_5k_seconds: float | None = None,
    experience_years: float = 0.0,
) -> tuple[UnifiedStateVector, AthleteState]:
    """Build S0 and the matching ORM row — does NOT touch the DB."""
    caps = _BASELINE_CAPACITIES.get(experience_level, _BASELINE_CAPACITIES["intermediate"])
    c_nm_force = squat_1rm_kg * 10.0 if squat_1rm_kg is not None else caps["c_nm_force"]
    # A supplied 5K time seeds aerobic capacity directly; otherwise use the table.
    c_met_aerobic = (
        _aerobic_from_run_5k(run_5k_seconds)
        if run_5k_seconds is not None
        else caps["c_met_aerobic"]
    )
    x = capacity_from_legacy(
        c_met_aerobic,
        c_nm_force,
        caps["c_struct"],
        caps["b_met_anaerobic"],
    )
    f = FatigueState()
    t = TissueState()

    u = build_unified_state_vector(
        timestamp=datetime.utcnow(),
        x=x,
        f=f,
        t=t,
        s_struct_signal=0.0,
        habit_strength=_habit_strength_from_experience(experience_years),
        skill_state=_baseline_skill_state(
            experience_level, squat_1rm_kg, deadlift_1rm_kg, bench_1rm_kg
        ),
    )
    row = AthleteState(user_id=user_id, **athlete_state_kwargs_from_unified(u))
    return u, row


async def initialize_athlete_state(
    db: AsyncSession,
    user_id: int,
    *,
    experience_level: str = "intermediate",
    squat_1rm_kg: float | None = None,
    deadlift_1rm_kg: float | None = None,
    bench_1rm_kg: float | None = None,
    bodyweight_kg: float | None = None,
    run_5k_seconds: float | None = None,
    experience_years: float = 0.0,
) -> UnifiedStateVector:
    """Creates baseline S0 for a new user and commits it."""
    _, row = _build_baseline_vector(
        user_id,
        experience_level,
        squat_1rm_kg,
        deadlift_1rm_kg,
        bench_1rm_kg,
        bodyweight_kg,
        run_5k_seconds,
        experience_years,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return unified_from_athlete_row(row)


async def load_current_state(
    db: AsyncSession, user_id: int
) -> UnifiedStateVector | None:
    """Load the athlete's current domain state, or None if none exists yet.

    The read half of the athlete-context load: fetch the latest row via the
    repository seam and convert it to a domain vector. Stays above the seam per
    CONTEXT.md (the repository returns rows, never vectors). Callers that need a
    baseline created on absence use load_or_init_current_state instead.
    """
    row = await AthleteContextRepository(db).get_latest_state(user_id)
    return unified_from_athlete_row(row) if row is not None else None


async def load_or_init_current_state(
    db: AsyncSession, user_id: int
) -> UnifiedStateVector:
    """Load the athlete's current domain state, auto-seeding a baseline if absent.

    Like load_current_state, but when no state exists yet it initializes (and
    commits) a baseline S0 and returns it. initialize_athlete_state already
    returns the converted vector, so there is no redundant re-fetch.
    """
    row = await AthleteContextRepository(db).get_latest_state(user_id)
    if row is None:
        return await initialize_athlete_state(db, user_id)
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


def _parse_sets(value: Any, default: float = 3.0) -> float:
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return default


def _parse_reps(value: Any, default: float = 8.0) -> float:
    """Reps may be a string range like '4-6' or '8-12/side' — take the first integer."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return float(m.group())
    return default


def _seed_exercises_from_prescription(
    log: WorkoutLog, planned_session: PlannedSession
) -> WorkoutLog:
    """Seed log.exercises from a planned session's prescribed content (ADR-0031).

    Gives planned work an exercise-aware dose (phi resolved by name) without the
    athlete re-entering every movement. Only fills when the client sent none.
    """
    content = planned_session.prescribed_content or {}
    prescribed = content.get("exercises") or []
    entries: list[ExerciseEntry] = []
    for ex in prescribed:
        name = ex.get("name") if isinstance(ex, dict) else None
        if not name:
            continue
        entries.append(
            ExerciseEntry(
                exercise_name=name,
                sets=_parse_sets(ex.get("sets")),
                reps=_parse_reps(ex.get("reps")),
            )
        )
    if not entries:
        return log
    return log.model_copy(update={"exercises": entries})


async def _match_planned_session(
    db: AsyncSession, user_id: int, log: WorkoutLog
) -> PlannedSession | None:
    """The planned session this log fulfills: explicit id, else same-day pending."""
    if log.planned_session_id is not None:
        res = await db.execute(
            select(PlannedSession).where(
                PlannedSession.id == log.planned_session_id,
                PlannedSession.user_id == user_id,
            )
        )
        return res.scalars().first()
    session_day = (
        log.timestamp.replace(tzinfo=None) if log.timestamp.tzinfo else log.timestamp
    ).date()
    res = await db.execute(
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.scheduled_date == session_day,
            PlannedSession.status == SessionStatus.PENDING,
        )
        .order_by(PlannedSession.id.asc())
        .limit(1)
    )
    return res.scalars().first()


async def process_new_workout(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
) -> UnifiedStateVector:
    """
    Fetch S(t), resolve exercise phi vectors, compute D(t), evolve to S(t+1), persist.
    """
    last_record = await AthleteContextRepository(db).get_latest_state(user_id)

    if not last_record:
        # Build and stage the baseline row without committing yet — the whole
        # operation (init + first workout) will commit atomically at the end.
        current_state, baseline_row = _build_baseline_vector(user_id)
        db.add(baseline_row)
    else:
        current_state = unified_from_athlete_row(last_record)

    # ADR-0031: a planned session's prescription seeds the log's exercises, so planned
    # work gets an exercise-aware dose without re-entry. Match the session up front
    # (also reused below for completion linkage).
    planned_session = await _match_planned_session(db, user_id, log)
    if not log.exercises and planned_session is not None:
        log = _seed_exercises_from_prescription(log, planned_session)

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
