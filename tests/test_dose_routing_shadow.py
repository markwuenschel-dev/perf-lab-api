"""Model B dose-routing shadow persistence (ADR-0054) — capture-only, no state impact.

Verifies that ingesting a workout writes one dose_routing_shadow_log row with full raw +
compat + provenance, stamped decision_impact="none_shadow_only", while the athlete state
advances exactly as before (state_update is untouched by Model B).
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.dose_routing_shadow import DoseRoutingShadowLog
from app.models.exercise import Exercise
from app.models.user import User
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services.state_service import process_new_workout

pytestmark = pytest.mark.asyncio


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
    db.add(Exercise(
        name="Easy Run", modality="Running", movement_pattern="run", load_type="distance",
    ))
    await db.commit()


def _mixed_log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=60.0,
        session_rpe=8.0,
        sets=[
            WorkoutSetEntry(exercise_name="Back Squat", sets=3, load_kg=100.0, reps=5, rpe=9.0),
            WorkoutSetEntry(exercise_name="Easy Run", distance_m=5000.0, duration_s=1500.0),
        ],
    )


async def test_ingest_writes_shadow_routing_row(async_db):
    user = await _user(async_db, "mb1@test.com")
    await _seed(async_db)

    result = await process_new_workout(async_db, user.id, _mixed_log())
    assert result is not None  # state advanced normally

    rows = (
        await async_db.execute(select(DoseRoutingShadowLog))
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.decision_impact == "none_shadow_only"     # never a live decision
    assert row.model_version == "dose_routing_compat_v1"
    assert row.calibration_basis == "sim_scenario_distribution_match_v1"
    # Squat resolves φ → exercise_phi tier; the run is a resolved unit too.
    assert row.routing_basis == "exercise_phi"
    assert row.n_units >= 2
    assert row.raw_fatigue_total > 0 and row.fatigue_compat_total > 0
    # k is versioned + persisted; compat = raw · k.
    assert row.k_json["fatigue"] == pytest.approx(24.4639, abs=1e-3)
    assert row.fatigue_compat_total == pytest.approx(
        row.raw_fatigue_total * row.k_json["fatigue"], rel=1e-6
    )
    assert isinstance(row.contributions_json, list) and row.contributions_json
    # The squat carries a structural signal; the run contributes none.
    squat = next(c for c in row.contributions_json if c["exercise_name"] == "Back Squat")
    assert squat["raw_struct"] > 0.0


async def test_shadow_failure_never_breaks_ingest(async_db, monkeypatch):
    """A shadow-write failure is swallowed — ingestion still returns state."""
    user = await _user(async_db, "mb2@test.com")
    await _seed(async_db)

    import app.services.dose_routing_shadow_service as svc

    async def _boom(*a, **k):
        raise RuntimeError("shadow boom")

    monkeypatch.setattr(svc, "_e1rm_by_exercise_key", _boom)
    result = await process_new_workout(async_db, user.id, _mixed_log())
    assert result is not None  # ingest survives a shadow failure
