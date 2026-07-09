import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.engine.phi_table import default_phi_for_row
from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    build_unified_state_vector,
    capacity_from_legacy,
    unified_from_athlete_row,
)
from app.logic import e1rm as e1rm_logic
from app.logic import strength_evidence as se
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import update_athlete_state
from app.models.athlete_state import AthleteState
from app.models.exercise import Exercise
from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.models.workout_set_log import WorkoutSetLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import ExerciseEntry, WorkoutLog, WorkoutSetEntry

logger = logging.getLogger(__name__)

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
    resolved_entries: list[ExerciseEntry] = []
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
    content: dict[str, Any] = planned_session.prescribed_content or {}
    prescribed: list[dict[str, Any]] = content.get("exercises") or []
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


async def _state_row_count(db: AsyncSession, user_id: int) -> int:
    """Number of persisted AthleteState rows for a user (1 ⇒ only the initial baseline)."""
    res = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(res.scalar_one())


# Exercise.modality → the WorkoutLog session-modality Literal. Anything without a
# direct session-level counterpart collapses to "Mixed".
_EXERCISE_TO_SESSION_MODALITY = {
    "Running": "Running",
    "Strength": "Strength",
    "Hypertrophy": "Hypertrophy",
    "Power": "Power",
    "Calisthenics": "Strength",
    "Conditioning": "Mixed",
    "Mixed": "Mixed",
}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def _resolve_set_exercises(
    db: AsyncSession, sets: list[WorkoutSetEntry]
) -> tuple[dict[int, Exercise], dict[str, Exercise]]:
    """Fetch the catalog rows referenced by a set list, by id and by name."""
    ids = [s.exercise_id for s in sets if s.exercise_id is not None]
    names = [s.exercise_name for s in sets if s.exercise_name and s.exercise_id is None]
    by_id: dict[int, Exercise] = {}
    by_name: dict[str, Exercise] = {}
    if ids:
        res = await db.execute(select(Exercise).where(Exercise.id.in_(ids)))
        by_id = {row.id: row for row in res.scalars().all()}
    if names:
        res = await db.execute(select(Exercise).where(Exercise.name.in_(names)))
        by_name = {row.name: row for row in res.scalars().all()}
    return by_id, by_name


async def _apply_sets_to_log(
    db: AsyncSession, log: WorkoutLog
) -> tuple[WorkoutLog, list[WorkoutSetLog], list[dict[str, Any]]]:
    """Materialize ``log.sets`` (ADR-0045) into persistable rows + a dose-ready log.

    Returns ``(updated_log, set_rows, e1rm_specs)``:

    * ``set_rows`` — one ``WorkoutSetLog`` per atomic set (a ``sets=N`` quick-entry
      expands to N rows), top set marked per exercise;
    * ``updated_log`` — the session with a **derived** modality (uniform → that
      modality, else Mixed), rolled-up volume/distance, and a synthesized
      per-exercise ``exercises`` breakdown so the dose reflects real external load;
    * ``e1rm_specs`` — ``{code, raw_value, rpe}`` for each loaded top set of a lift
      carrying an ``e1rm_benchmark_code``, written after commit as observations.
    """
    by_id, by_name = await _resolve_set_exercises(db, log.sets)

    # Group key → (exercise row | None, list of materialized rows). A group is one
    # exercise (or one free-text movement) across the whole session.
    groups: dict[str, tuple[Exercise | None, list[WorkoutSetLog]]] = {}
    group_order: list[str] = []
    # A group whose rows were cloned from a sets=N quick-entry carries group-level
    # effort, not true per-set effort (ADR-0045) — lowers e1RM evidence authority.
    group_quick: dict[str, bool] = {}
    set_rows: list[WorkoutSetLog] = []
    set_index = 0

    for entry in log.sets:
        ex_row: Exercise | None = None
        if entry.exercise_id is not None:
            ex_row = by_id.get(entry.exercise_id)
        elif entry.exercise_name:
            ex_row = by_name.get(entry.exercise_name)

        load_type = entry.load_type or (ex_row.load_type if ex_row else None)
        name = ex_row.name if ex_row else entry.exercise_name
        free_text = None if ex_row else (entry.free_text_name or entry.exercise_name)
        key = f"id:{ex_row.id}" if ex_row else f"free:{free_text or name or 'unknown'}"
        if key not in groups:
            groups[key] = (ex_row, [])
            group_order.append(key)
        group_quick[key] = group_quick.get(key, False) or (entry.sets > 1)

        for _ in range(max(1, entry.sets)):
            row = WorkoutSetLog(
                set_index=set_index,
                exercise_id=ex_row.id if ex_row else None,
                free_text_name=free_text,
                load_type=load_type,
                load_kg=entry.load_kg,
                reps=entry.reps,
                duration_s=entry.duration_s,
                distance_m=entry.distance_m,
                rpe=entry.rpe,
                rir=entry.rir,
                is_top_set=bool(entry.is_top_set),
                band=entry.band,
                elevation=entry.elevation,
                tempo=entry.tempo,
                notes=entry.notes,
            )
            groups[key][1].append(row)
            set_rows.append(row)
            set_index += 1

    # Per exercise: mark exactly one top set (heaviest loaded set, ties → last),
    # unless the client already forced one. Drives e1RM extraction.
    e1rm_specs: list[dict[str, Any]] = []
    session_modalities: list[str] = []
    synthesized: list[ExerciseEntry] = []
    total_volume = 0.0
    total_distance = 0.0

    for key in group_order:
        ex_row, rows = groups[key]
        loaded_rows = [
            r for r in rows if e1rm_logic.is_loaded(r.load_type) and r.load_kg is not None
        ]
        if not any(r.is_top_set for r in rows) and loaded_rows:
            top = max(loaded_rows, key=lambda r: (r.load_kg or 0.0))
            # ties → last submitted
            top = [r for r in loaded_rows if (r.load_kg or 0.0) == (top.load_kg or 0.0)][-1]
            top.is_top_set = True

        top_set = next((r for r in rows if r.is_top_set), None)
        fidelity = "group_level" if group_quick.get(key) else "set_level"
        # Extraction gate (ADR-0055): only low-rep, high-effort top sets of a mapped lift
        # yield e1RM evidence — and it is always estimated/lower-bound, never capacity.
        if (
            top_set is not None
            and ex_row is not None
            and ex_row.e1rm_benchmark_code
            and top_set.load_kg is not None
            and top_set.reps is not None
            and se.is_e1rm_informative(top_set.reps, top_set.rpe, top_set.rir, fidelity)
        ):
            e1rm_specs.append(
                {
                    "code": ex_row.e1rm_benchmark_code,
                    "raw_value": round(
                        e1rm_logic.epley_e1rm(top_set.load_kg, top_set.reps), 1
                    ),
                    "exercise_id": ex_row.id,
                    "reps": top_set.reps,
                    "rpe": top_set.rpe,
                    "rir": top_set.rir,
                    "load_kg": top_set.load_kg,
                    "effort_fidelity": fidelity,
                }
            )

        if ex_row is not None:
            mapped = _EXERCISE_TO_SESSION_MODALITY.get(ex_row.modality, "Mixed")
            session_modalities.append(mapped)

        # Volume-load + distance roll-ups from real sets.
        for r in rows:
            if r.load_kg is not None and r.reps is not None:
                total_volume += r.load_kg * r.reps
            if r.distance_m is not None:
                total_distance += r.distance_m

        # Synthesize a per-exercise entry so the dose engine sees real external load.
        loads = [r.load_kg for r in rows if r.load_kg is not None]
        reps = [float(r.reps) for r in rows if r.reps is not None]
        durations = [r.duration_s for r in rows if r.duration_s is not None]
        distances = [r.distance_m for r in rows if r.distance_m is not None]
        rpes = [r.rpe for r in rows if r.rpe is not None]
        rirs = [r.rir for r in rows if r.rir is not None]
        synthesized.append(
            ExerciseEntry(
                exercise_id=ex_row.id if ex_row else None,
                exercise_name=(ex_row.name if ex_row else None),
                sets=float(len(rows)),
                reps=_mean(reps),
                load_kg=_mean(loads),
                duration_seconds=(sum(durations) if durations else None),
                distance_meters=(sum(distances) if distances else None),
                avg_rpe=_mean(rpes),
                avg_rir=_mean(rirs),
            )
        )

    # Derived session modality: uniform → that modality, else Mixed. Falls back to
    # the client-supplied modality when no set resolved to a catalog exercise.
    distinct = set(session_modalities)
    if len(distinct) == 1:
        derived_modality = distinct.pop()
    elif distinct:
        derived_modality = "Mixed"
    else:
        derived_modality = log.modality

    updated = log.model_copy(
        update={
            "modality": derived_modality,
            "exercises": synthesized,
            "total_volume_load": round(total_volume, 2) if total_volume else log.total_volume_load,
            "distance_meters": round(total_distance, 2) if total_distance else log.distance_meters,
        }
    )
    return updated, set_rows, e1rm_specs


async def _e1rm_watermark(db: AsyncSession, user_id: int, code: str) -> float | None:
    """Highest e1RM observed for this code (demonstrated high-watermark).

    Excludes quarantined/invalid rows. Used to keep training-derived evidence
    upward-only — a set below the watermark is history, never a lower bound.
    """
    from app.models.benchmark_definition import BenchmarkDefinition
    from app.models.benchmark_observation import BenchmarkObservation

    res = await db.execute(
        select(func.max(BenchmarkObservation.raw_value))
        .join(
            BenchmarkDefinition,
            BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id,
        )
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == code,
            BenchmarkObservation.validity_status.notin_(("quarantined", "invalid")),
        )
    )
    return res.scalar_one_or_none()


async def _extract_e1rm_observations(
    db: AsyncSession,
    user_id: int,
    specs: list[dict[str, Any]],
    observed_at: datetime,
    workout_log_id: int | None,
) -> int:
    """Write training-derived e1RM evidence from gated top sets (ADR-0055).

    This is **estimated / lower-bound** evidence, never a capacity measurement — it
    can raise a lower-bound floor (a PR beyond a small deadband) but never regresses
    capacity (enforced by ``benchmark_service`` + the DB guard). Runs *after* the
    workout state commit; best-effort per spec.
    """
    from app.schemas.benchmarks import BenchmarkObservationCreate
    from app.services import benchmark_service

    written = 0
    for spec in specs:
        try:
            watermark = await _e1rm_watermark(db, user_id, spec["code"])
            is_pr = watermark is None or spec["raw_value"] > watermark * 1.005
            fidelity = spec.get("effort_fidelity", "set_level")
            await benchmark_service.create_observation(
                db,
                user_id,
                BenchmarkObservationCreate(
                    benchmark_code=spec["code"],
                    raw_value=spec["raw_value"],
                    observed_at=observed_at,
                    source=se.SOURCE_WORKOUT_EXTRACTION,
                    evidence_type=(
                        se.EV_LOWER_BOUND if is_pr else se.EV_ESTIMATED_FROM_TRAINING_SET
                    ),
                    value_semantics=(se.VS_LOWER_BOUND if is_pr else se.VS_ESTIMATED),
                    # A below-watermark set is history only — not even a prescription basis.
                    affects_prescription=is_pr,
                    observation_weight=(0.10 if is_pr else 0.0),
                    confidence=(0.15 if fidelity == "group_level" else 0.30) if is_pr else None,
                    exercise_id=spec.get("exercise_id"),
                    workout_log_id=workout_log_id,
                    reps=spec.get("reps"),
                    load_kg=spec.get("load_kg"),
                    rpe=spec.get("rpe"),
                    rir=spec.get("rir"),
                    formula="epley",
                    effort_fidelity=fidelity,
                ),
            )
            written += 1
        except Exception:
            logger.warning(
                "e1RM extraction failed for user %s code %s",
                user_id, spec.get("code"), exc_info=True,
            )
    return written


async def process_new_workout(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
) -> UnifiedStateVector:
    """
    Fetch S(t), resolve exercise phi vectors, compute D(t), evolve to S(t+1), persist.
    """
    last_record = await AthleteContextRepository(db).get_latest_state(user_id)

    # UTC-naive workout time — the anchor for this state transition. The DB stores
    # naive datetimes; log.timestamp may arrive tz-aware.
    log_ts = log.timestamp.replace(tzinfo=None) if log.timestamp.tzinfo else log.timestamp

    if not last_record:
        # Build and stage the baseline row without committing yet — the whole
        # operation (init + first workout) will commit atomically at the end.
        current_state, initial_baseline = _build_baseline_vector(user_id)
        db.add(initial_baseline)
    else:
        current_state = unified_from_athlete_row(last_record)
        # S0 is stamped at the wall-clock time the account was created, which is
        # unrelated to the athlete's training timeline. If it is still the only
        # state row, it is the initial baseline and gets re-anchored below.
        initial_baseline = last_record if await _state_row_count(db, user_id) == 1 else None

    # Anchor the initial baseline to just before the athlete's first training event so
    # the timeline is ordered by workouts, not by account-creation time. Without this a
    # workout logged at/before S0 (backfill, wearable-history import, clock skew)
    # collapses every later row's timestamp onto S0's and breaks recency ordering.
    if initial_baseline is not None:
        anchor = log_ts - timedelta(seconds=1)
        initial_baseline.timestamp = anchor
        current_state.timestamp = anchor

    # ADR-0031: a planned session's prescription seeds the log's exercises, so planned
    # work gets an exercise-aware dose without re-entry. Match the session up front
    # (also reused below for completion linkage).
    planned_session = await _match_planned_session(db, user_id, log)
    if not log.exercises and not log.sets and planned_session is not None:
        log = _seed_exercises_from_prescription(log, planned_session)

    # ADR-0045: per-set logging. When the client sends atomic sets, they are the
    # record — materialize the rows, derive the session modality, roll up volume,
    # and synthesize a per-exercise breakdown so the dose reflects real external
    # load. Top sets yield e1RM observations after the workout commits.
    set_rows: list[WorkoutSetLog] = []
    e1rm_specs: list[dict[str, Any]] = []
    if log.sets:
        log, set_rows, e1rm_specs = await _apply_sets_to_log(db, log)

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
    # Attach materialized set rows; the relationship cascade inserts them.
    if set_rows:
        workout_row.set_logs = set_rows
    db.add(workout_row)
    await db.flush()
    workout_log_id = workout_row.id  # capture before commit expires the object

    if planned_session is not None:
        planned_session.workout_log_id = workout_row.id
        planned_session.status = SessionStatus.COMPLETED
        planned_session.completed_at = datetime.utcnow()
        workout_row.planned_session_id = planned_session.id

    # Physical decay interval since the current state, clamped non-negative so an
    # out-of-order/backfilled log never applies negative decay.
    state_ts = current_state.timestamp.replace(tzinfo=None) if current_state.timestamp.tzinfo else current_state.timestamp
    dt = timedelta(seconds=0) if log_ts < state_ts else log_ts - state_ts

    new_state_schema = update_athlete_state(current_state, dose, dt, log)
    # The evolved state is valid "as of" the workout event; anchor its timestamp to the
    # workout time. Identical to the engine's prev+dt in the normal forward case, and
    # correct when dt was clamped for a historical log (keeps the timeline event-ordered).
    new_state_schema.timestamp = log_ts

    kwargs = athlete_state_kwargs_from_unified(new_state_schema)
    new_db_record = AthleteState(user_id=user_id, **kwargs)
    db.add(new_db_record)
    await db.commit()
    await db.refresh(new_db_record)

    # Materialize the return value BEFORE the shadow write: the EKF's best-effort
    # commit/rollback expires ORM objects, which would break a later attribute read.
    result = unified_from_athlete_row(new_db_record)

    # Shadow EKF (ADR-0041): advance the parallel full-covariance belief through this
    # same workout. Best-effort and capture-only — never affects the returned state.
    from app.services import ekf_shadow_service

    await ekf_shadow_service.record_ekf_predict(db, user_id, dose, dt, log)

    # ADR-0045: write-time e1RM extraction. Top sets become benchmark observations
    # (the measurement layer, PDR-0003) — never read back by scanning set logs. Runs
    # after the workout commit; each observation advances max_strength on its own, so
    # the returned state is re-materialized to reflect them.
    if e1rm_specs:
        written = await _extract_e1rm_observations(
            db, user_id, e1rm_specs, log_ts, workout_log_id
        )
        if written:
            latest = await AthleteContextRepository(db).get_latest_state(user_id)
            if latest is not None:
                result = unified_from_athlete_row(latest)

    return result
