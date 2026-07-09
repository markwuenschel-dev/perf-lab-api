"""P9 — per-set logging + strength loop (ADR-0045).

Covers the four phase outcomes: sets persist to workout_set_logs with a derived
session modality and a marked top set; a top set emits an e1RM benchmark
observation (measurement layer, not a set-log scan); the sets feed real external
load into the dose; and a prescribed lift resolves %e1RM → a suggested kg.
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.logic import e1rm
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.models.workout_set_log import WorkoutSetLog
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services import benchmark_service
from app.services.prescription_service import _enrich_exercises_with_load
from app.services.state_service import _apply_sets_to_log, process_new_workout

pytestmark = pytest.mark.asyncio


# ── e1RM math (no DB) ─────────────────────────────────────────────────────────

async def test_epley_and_percent_are_inverse_shaped():
    # 100 kg × 5 reps → 100 × (1 + 4/30) ≈ 113.3 kg estimated 1RM
    assert e1rm.epley_e1rm(100.0, 5) == pytest.approx(100.0 * (1 + 4 / 30))
    assert e1rm.epley_e1rm(120.0, 1) == pytest.approx(120.0)  # a single is its own e1RM
    assert e1rm.percent_1rm(1, 10.0) == pytest.approx(1.0)  # 1 rep to failure = 100%
    # an RPE cap leaves reps in reserve → a lighter %1RM
    assert e1rm.percent_1rm(5, 8.0) < e1rm.percent_1rm(5, None)


async def test_suggested_load_is_plate_rounded_and_loaded_types():
    load = e1rm.suggested_load_kg(140.0, 5, 8.0)
    assert load % 2.5 == 0
    assert 0 < load < 140.0
    assert e1rm.is_loaded("barbell") and not e1rm.is_loaded("distance")


# ── DB seeding ────────────────────────────────────────────────────────────────

async def _create_user(db, email: str) -> User:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_catalog(db, with_e1rm_def: bool = False) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
    db.add(Exercise(
        name="Easy Run", modality="Running", movement_pattern="run", load_type="distance",
    ))
    if with_e1rm_def:
        db.add(BenchmarkDefinition(
            code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
            metric_type="load", unit="kg", better_direction="higher",
            observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
        ))
    await db.commit()


def _mixed_log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC),
        modality="Strength",  # client-supplied — should be overridden to the derived Mixed
        duration_minutes=60.0,
        session_rpe=8.0,
        sets=[
            WorkoutSetEntry(exercise_name="Back Squat", sets=3, load_kg=100.0, reps=5, rpe=8.0),
            WorkoutSetEntry(exercise_name="Easy Run", distance_m=5000.0, duration_s=1500.0),
        ],
    )


# ── persistence + derived modality + top set ──────────────────────────────────

async def test_sets_persist_with_derived_modality_and_top_set(async_db):
    user = await _create_user(async_db, "p9a@test.com")
    await _seed_catalog(async_db)

    await process_new_workout(async_db, user.id, _mixed_log())

    rows = (
        await async_db.execute(select(WorkoutSetLog).order_by(WorkoutSetLog.set_index))
    ).scalars().all()
    assert len(rows) == 4  # 3 squat sets materialized + 1 run set
    assert [r.set_index for r in rows] == [0, 1, 2, 3]

    squat_rows = [r for r in rows if r.load_type == "barbell"]
    assert len(squat_rows) == 3
    assert sum(1 for r in squat_rows if r.is_top_set) == 1  # exactly one top set

    wl = (await async_db.execute(select(WorkoutLogORM))).scalars().first()
    assert wl is not None
    assert wl.modality == "Mixed"  # Strength + Running → derived Mixed
    assert wl.total_volume_load == pytest.approx(1500.0)  # 3 × 100 × 5
    assert wl.distance_meters == pytest.approx(5000.0)


async def test_sets_feed_external_load_into_dose(async_db):
    """The synthesized per-exercise breakdown carries real reps/load, which is how
    external load (ADR-0039 I) reaches the exercise-aware dose path."""
    await _seed_catalog(async_db)

    updated, set_rows, e1rm_specs = await _apply_sets_to_log(async_db, _mixed_log())

    assert len(set_rows) == 4  # 3 squat + 1 run materialized
    assert [s["code"] for s in e1rm_specs] == ["pl_e1rm_squat"]  # top set → e1RM spec
    squat = next(e for e in updated.exercises if e.exercise_name == "Back Squat")
    assert squat.reps == 5 and squat.load_kg == 100.0 and squat.sets == 3.0
    # A session-only log has no external load (I=1); the set-fed log routes through
    # the exercise path instead — the dose is genuinely exercise-aware, not identical.
    session_only = _mixed_log().model_copy(update={"sets": [], "exercises": []})
    assert calculate_stress_dose(updated).dose_six != calculate_stress_dose(session_only).dose_six


# ── write-time e1RM extraction (measurement layer) ────────────────────────────

async def test_top_set_emits_e1rm_observation(async_db):
    user = await _create_user(async_db, "p9b@test.com")
    await _seed_catalog(async_db, with_e1rm_def=True)

    await process_new_workout(async_db, user.id, _mixed_log())

    obs = (
        await async_db.execute(
            select(BenchmarkObservation).where(
                BenchmarkObservation.source == "workout_extraction"
            )
        )
    ).scalars().all()
    assert len(obs) == 1
    assert obs[0].raw_value == pytest.approx(e1rm.epley_e1rm(100.0, 5), abs=0.1)


async def test_no_e1rm_code_no_extraction(async_db):
    """A loaded lift without an e1rm_benchmark_code emits no observation."""
    user = await _create_user(async_db, "p9f@test.com")
    async_db.add(Exercise(
        name="Leg Press", modality="Hypertrophy", movement_pattern="squat",
        load_type="machine",  # no e1rm_benchmark_code
    ))
    await async_db.commit()

    log = WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=45.0,
        session_rpe=7.0,
        sets=[WorkoutSetEntry(exercise_name="Leg Press", sets=3, load_kg=200.0, reps=10, rpe=7.0)],
    )
    await process_new_workout(async_db, user.id, log)

    obs = (await async_db.execute(select(BenchmarkObservation))).scalars().all()
    assert obs == []


# ── prescription speaks in load ───────────────────────────────────────────────

def _squat_rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="strength", focus="squat", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(
            name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE"
        )],
    )


async def test_prescription_resolves_percent_e1rm_to_kg(async_db):
    user = await _create_user(async_db, "p9d@test.com")
    await _seed_catalog(async_db, with_e1rm_def=True)
    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(benchmark_code="pl_e1rm_squat", raw_value=140.0),
    )

    rx = _squat_rx()
    await _enrich_exercises_with_load(
        async_db, user.id, rx, {"week_number": 1, "duration_weeks": 4}
    )

    ex = rx.exercises[0]
    assert ex.e1rm_basis_kg == 140.0
    assert ex.rpe_cap == 7.5  # accumulation (week 1 of 4) → rpe_high 7.5
    assert ex.percent_e1rm == pytest.approx(e1rm.percent_1rm(5, 7.5), abs=1e-3)
    assert ex.prescribed_load_kg == e1rm.suggested_load_kg(140.0, 5, 7.5)
    assert "e1RM" in (ex.load_note or "")


async def test_prescription_falls_back_to_rpe_without_e1rm(async_db):
    user = await _create_user(async_db, "p9e@test.com")
    await _seed_catalog(async_db, with_e1rm_def=True)  # definition exists, no observation

    rx = _squat_rx()
    await _enrich_exercises_with_load(async_db, user.id, rx, {})

    ex = rx.exercises[0]
    assert ex.prescribed_load_kg is None
    assert ex.percent_e1rm is None
    assert ex.load_note == "Autoregulate by RPE"  # unchanged
