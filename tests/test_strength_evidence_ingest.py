"""S2 at ingest: training evidence is prescription-eligible without touching capacity.

Workout extraction used to set ``affects_prescription = is_pr``, so a gated top set below
the all-time watermark could never size a prescription — the capacity-protecting rule was
doing a second, unrelated job. These tests pin the separation from both sides: the
capacity labels and the watermark are exactly what they were, the prescription permission
is no longer tied to beating the watermark, and "latest lighter set wins" is not what
replaces it. They also pin, through real ingestion rather than the pure selector, that the
workout's own INSTANT becomes the performance time whatever offset it arrives in, that set
rows persist the effort fidelity ingest already inferred, and how the dose-intensity
calculation changes when the e1RM denominator expires.
"""
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.logic import strength_calibration as sc
from app.logic.prescription_evidence import STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.models.workout_set_log import WorkoutSetLog
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services.prescription_service import _enrich_exercises_with_load
from app.services.state_service import (
    best_currently_validated_e1rm,
    prelog_e1rm_denominators,
    process_new_workout,
)

pytestmark = pytest.mark.asyncio

_CODE = "pl_e1rm_squat"
_WINDOW = timedelta(days=STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS)
#: The first workout, an aware UTC instant; everything else is placed relative to it.
T1 = (datetime.now(UTC) - timedelta(days=40)).replace(microsecond=0)


async def _athlete(db, email: str) -> User:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code=_CODE,
    ))
    db.add(Exercise(name="Easy Run", modality="Running", movement_pattern="run", load_type="distance"))
    db.add(BenchmarkDefinition(
        code=_CODE, name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
    ))
    await db.commit()
    await db.refresh(user)
    return user


def _squat_session(
    at: datetime, *, load: float, reps: int, rpe: float | None
) -> WorkoutLog:
    return WorkoutLog(
        timestamp=at, modality="Strength", duration_minutes=45.0, session_rpe=rpe or 7.0,
        sets=[WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=load, reps=reps, rpe=rpe)],
    )


async def _extracted(db, user_id: int) -> list[BenchmarkObservation]:
    return list((await db.execute(
        select(BenchmarkObservation)
        .where(BenchmarkObservation.user_id == user_id, BenchmarkObservation.source == "workout_extraction")
        .order_by(BenchmarkObservation.id)
    )).scalars().all())


async def _squat_basis(db, user_id: int, as_of: datetime) -> float | None:
    rx = WorkoutPrescription(
        type="strength", focus="squat", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE")],
    )
    await _enrich_exercises_with_load(db, user_id, rx, {"week_number": 1, "duration_weeks": 4}, as_of=as_of)
    return rx.exercises[0].e1rm_basis_kg


async def _session_intensities(db, user_id: int) -> list[dict]:
    """The external-intensity snapshot each logged session's dose was computed with."""
    rows = (await db.execute(
        select(WorkoutLogORM).where(WorkoutLogORM.user_id == user_id).order_by(WorkoutLogORM.session_timestamp)
    )).scalars().all()
    return [row.dose_snapshot["external_intensity"] for row in rows]


# ── prescription permission vs capacity ──────────────────────────────────────────

async def test_a_gated_set_below_the_watermark_is_eligible_and_capacity_is_unchanged(async_db):
    user = await _athlete(async_db, "s2-ingest-decouple@test.com")
    await process_new_workout(async_db, user.id, _squat_session(T1, load=150.0, reps=1, rpe=9.5))
    watermark_before = await best_currently_validated_e1rm(async_db, user.id, _CODE)

    t2 = T1 + timedelta(days=1)
    await process_new_workout(async_db, user.id, _squat_session(t2, load=120.0, reps=3, rpe=9.0))
    pr, below = await _extracted(async_db, user.id)

    # Capacity side: exactly as before S2.
    assert watermark_before == pytest.approx(150.0)
    assert await best_currently_validated_e1rm(async_db, user.id, _CODE) == pytest.approx(150.0)
    assert below.raw_value == pytest.approx(sc.epley_e1rm(120.0, 3), abs=0.1)
    assert (below.evidence_type, below.value_semantics) == ("estimated_from_training_set", "estimated")
    assert (pr.evidence_type, pr.value_semantics) == ("lower_bound", "lower_bound")
    assert below.observation_weight == 0.0 and below.confidence is None

    # Prescription side: permission no longer depends on beating the watermark.
    assert below.affects_prescription is True


async def test_the_in_window_maximum_is_the_basis_not_the_latest_lighter_set(async_db):
    user = await _athlete(async_db, "s2-ingest-max@test.com")
    await process_new_workout(async_db, user.id, _squat_session(T1, load=150.0, reps=1, rpe=9.5))
    t2 = T1 + timedelta(days=1)
    await process_new_workout(async_db, user.id, _squat_session(t2, load=120.0, reps=3, rpe=9.0))

    assert await _squat_basis(async_db, user.id, as_of=t2 + timedelta(hours=1)) == pytest.approx(150.0)
    # Once the 150 is past 28 days and the lighter set is not, the lighter set is the basis.
    after_expiry = T1 + timedelta(days=28, hours=1)
    assert await _squat_basis(async_db, user.id, as_of=after_expiry) == pytest.approx(
        sc.epley_e1rm(120.0, 3), abs=0.1
    )


# ── performance time ─────────────────────────────────────────────────────────────

async def test_extraction_stamps_the_workout_time_as_performance_time(async_db):
    user = await _athlete(async_db, "s2-ingest-performed@test.com")
    backdated = T1 - timedelta(days=2)
    await process_new_workout(async_db, user.id, _squat_session(backdated, load=140.0, reps=2, rpe=9.0))

    (row,) = await _extracted(async_db, user.id)
    assert row.performed_at == backdated.replace(tzinfo=None)


async def test_an_offset_workout_time_is_its_utc_instant_and_expires_on_that_instant(async_db):
    """10:00+05:00 is 05:00 UTC. Ingestion must store the instant, not the wall clock, and
    both readers must expire the evidence exactly one window after that instant — whatever
    offset the reference time itself is spelled in."""
    user = await _athlete(async_db, "s2-ingest-offset@test.com")
    plus_five = timezone(timedelta(hours=5))
    await process_new_workout(
        async_db, user.id, _squat_session(T1.astimezone(plus_five), load=140.0, reps=2, rpe=9.0)
    )

    (row,) = await _extracted(async_db, user.id)
    instant = T1.replace(tzinfo=None)
    assert row.performed_at == instant

    edge = T1 + _WINDOW
    assert await _squat_basis(async_db, user.id, as_of=edge.astimezone(plus_five)) is not None
    assert await _squat_basis(async_db, user.id, as_of=edge + timedelta(seconds=1)) is None
    assert _CODE in await prelog_e1rm_denominators(
        async_db, user.id, {_CODE}, as_of=edge.astimezone(plus_five)
    )
    assert _CODE not in await prelog_e1rm_denominators(
        async_db, user.id, {_CODE}, as_of=(edge + timedelta(seconds=1)).astimezone(plus_five)
    )


# ── the dose denominator after expiry ────────────────────────────────────────────

async def test_expiry_changes_the_dose_intensity_method_through_real_ingestion(async_db):
    """Keeps the existing fallback policy and pins what expiry actually does to it: the
    calculation METHOD changes (relative load -> RPE chart when effort is present ->
    labelled neutral 1.0 when it is not). The value can move either way; the neutral
    reading's zero confidence does not remove its numeric contribution."""
    user = await _athlete(async_db, "s2-ingest-dose@test.com")
    # Evidence: a qualifying single, performed at T1.
    await process_new_workout(async_db, user.id, _squat_session(T1, load=150.0, reps=1, rpe=9.5))
    # Fresh denominator: scored against it.
    await process_new_workout(
        async_db, user.id, _squat_session(T1 + timedelta(days=1), load=120.0, reps=6, rpe=8.0)
    )
    # Expired, effort present (6 reps: no new evidence is extracted from these sessions).
    await process_new_workout(
        async_db, user.id, _squat_session(T1 + _WINDOW + timedelta(days=2), load=120.0, reps=6, rpe=8.0)
    )
    # Expired, effort missing.
    await process_new_workout(
        async_db, user.id, _squat_session(T1 + _WINDOW + timedelta(days=3), load=120.0, reps=6, rpe=None)
    )

    _, fresh, expired_with_effort, expired_without_effort = await _session_intensities(async_db, user.id)

    (c,) = fresh["contributions"]
    assert c["source"] == sc.SRC_RELATIVE_LOAD
    assert c["e1rm_denominator_kg"] == pytest.approx(150.0)
    assert c["external_intensity"] == pytest.approx(120.0 / 150.0, abs=1e-3)

    (c,) = expired_with_effort["contributions"]
    chart = sc.external_intensity_for_set(
        reps=6, load_kg=120.0, rpe=8.0, rir=None, e1rm_pre=None, to_failure=False,
        effort_fidelity="set_level",
    )
    assert c["source"] == sc.SRC_RPE_RIR_CHART
    assert c["e1rm_denominator_kg"] is None and c["e1rm_observation_id"] is None
    assert c["external_intensity"] == pytest.approx(chart.value, abs=1e-3)

    (c,) = expired_without_effort["contributions"]
    assert c["source"] == sc.SRC_NEUTRAL_MISSING
    assert c["external_intensity"] == pytest.approx(1.0)
    assert c["confidence"] == 0.0
    assert expired_without_effort["value"] == pytest.approx(1.0)


# ── effort provenance on set rows ────────────────────────────────────────────────

async def test_set_rows_persist_the_effort_fidelity_ingest_inferred(async_db):
    """Persisted exactly as computed today — one label per exercise across the session,
    group_level when any entry for it was a sets>1 quick-entry — plus which submitted
    entry each row was cloned from, so per-entry reprocessing is possible later."""
    user = await _athlete(async_db, "s2-ingest-fidelity@test.com")
    log = WorkoutLog(
        timestamp=T1, modality="Strength", duration_minutes=60.0, session_rpe=8.0,
        sets=[
            WorkoutSetEntry(exercise_name="Back Squat", sets=3, load_kg=100.0, reps=5, rpe=9.0),
            WorkoutSetEntry(exercise_name="Back Squat", sets=1, load_kg=110.0, reps=3, rpe=9.0),
            WorkoutSetEntry(exercise_name="Easy Run", distance_m=5000.0, duration_s=1500.0),
        ],
    )
    await process_new_workout(async_db, user.id, log)

    rows = (await async_db.execute(select(WorkoutSetLog).order_by(WorkoutSetLog.set_index))).scalars().all()
    assert [(r.entry_group_id, r.effort_fidelity) for r in rows] == [
        (0, "group_level"), (0, "group_level"), (0, "group_level"),
        (1, "group_level"),
        (2, "set_level"),
    ]
