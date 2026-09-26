"""Phase 8A: dose model v1 captured beside production v0 — never applied.

Three guarantees, each tested here:

* **Capture is complete.** Every ingested workout writes one row with both doses, their ratio,
  the density and volume-set provenance of each, the pre-session state, and the versions that
  produced the observation.
* **Capture is inert.** The athlete state is built from v0 exactly as before; the row says
  ``none_shadow_only`` and the persisted dose snapshot is v0's.
* **Capture is disposable.** A failure computing or writing v1 must never break logging a
  workout.

The provenance columns are the point of the dataset: phase 8B has to separate "v1 differs
because density was corrected" from "v1 differs because it had no endurance-density input".
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.logic import dose_engine_v0, dose_engine_v1
from app.logic.state_update_v0 import STATE_UPDATE_MODEL_VERSION
from app.models.athlete_state import AthleteState
from app.models.dose_model_shadow import DoseModelShadowLog
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import dose_model_shadow_service as svc
from app.services.state_service import process_new_workout

_WHEN = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": _WHEN,
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.5,
        "estimated_sets": 20.0,
        "total_volume_load": 8000.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _state():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "properties"))
    from test_state_invariants import _state as state

    return state()


def _row(log: WorkoutLog, **kwargs) -> DoseModelShadowLog:
    return svc.build_shadow_row(
        user_id=1,
        workout_log_id=None,
        log=log,
        v0_dose=dose_engine_v0.calculate_stress_dose(log),
        v1_dose=dose_engine_v1.calculate_stress_dose(log),
        state_before=_state(),
        session_at=_WHEN,
        **kwargs,
    )


# ── pure row construction (no database) ───────────────────────────────────────

def test_a_reported_strength_session_records_measured_density_on_both_sides() -> None:
    row = _row(_log())

    assert row.v0_density_basis == "legacy_minutes_per_set"
    assert row.v1_density_basis == "sets_per_elapsed_minute"
    assert row.v1_density_value is not None
    assert row.v1_density_not_modelled is False
    assert row.v0_volume_used_fabricated_sets is False
    assert row.ratio_v1_v0 == pytest.approx(row.v1_total / row.v0_total)


def test_a_run_is_marked_as_not_modelled_rather_than_as_corrected() -> None:
    """The distinction phase 5 depends on: absent endurance density ≠ corrected density."""
    row = _row(_log(modality="Running", estimated_sets=None, total_volume_load=0.0))

    assert row.v1_density_not_modelled is True
    assert row.v1_density_basis == "not_applicable"
    assert row.v1_density_value is None
    assert row.v0_volume_used_fabricated_sets is True
    assert row.v0_volume_sets_basis == "fabricated_fallback"
    assert row.v1_volume_sets_basis == "not_counted"


def test_the_ratio_is_undefined_rather_than_infinite_when_v0_carried_no_dose() -> None:
    log = _log()
    row = svc.build_shadow_row(
        user_id=1,
        workout_log_id=None,
        log=log,
        v0_dose=StressDose(),
        v1_dose=dose_engine_v1.calculate_stress_dose(log),
        state_before=_state(),
        session_at=_WHEN,
    )

    assert row.v0_total == 0.0
    assert row.ratio_v1_v0 is None


def test_every_row_names_the_models_and_the_chronology_that_produced_it() -> None:
    row = _row(_log())

    assert row.v0_model_version == "v0"
    assert row.v1_model_version == "v1.1"
    assert row.state_update_model == STATE_UPDATE_MODEL_VERSION
    assert row.prescription_engine_version
    assert row.decision_impact == "none_shadow_only"


def test_the_row_records_the_state_the_athlete_brought_to_the_session() -> None:
    row = _row(_log())

    assert set(row.state_before_json) >= {"capacity", "fatigue", "tissue"}
    assert row.state_before_json["capacity"]["max_strength"] == pytest.approx(50.0)


def test_code_version_is_recorded_only_when_the_deploy_provides_one(monkeypatch) -> None:
    monkeypatch.delenv(svc.BUILD_SHA_ENV, raising=False)
    assert _row(_log()).code_version is None

    monkeypatch.setenv(svc.BUILD_SHA_ENV, "abc1234")
    assert _row(_log()).code_version == "abc1234"


def test_planned_slot_provenance_travels_when_the_session_fulfilled_one() -> None:
    row = _row(_log(), planned_domain="powerlifting", planned_category="SBD Strength")

    assert row.planned_domain == "powerlifting"
    assert row.planned_category == "SBD Strength"


# ── through the real ingest path (database) ───────────────────────────────────

async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_ingest_writes_one_shadow_row_and_state_still_comes_from_v0(async_db) -> None:
    user = await _user(async_db, "shadow8a@test.com")

    result = await process_new_workout(async_db, user.id, _log())
    assert result is not None

    rows = (await async_db.execute(select(DoseModelShadowLog))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.decision_impact == "none_shadow_only"
    assert row.user_id == user.id

    # The persisted dose — the one state was built from — is v0's, and the shadow row's v0
    # side is that same dose, not a recomputation that could drift from it.
    logged = (await async_db.execute(select(WorkoutLogORM))).scalars().one()
    assert logged.dose_snapshot["dose_model_version"] == "v0"
    persisted_v0_total = sum(logged.dose_snapshot["dose_six"].values())
    assert row.v0_total == pytest.approx(persisted_v0_total)
    assert row.workout_log_id == logged.id

    # The state written for this workout records the corrected chronology.
    states = (await async_db.execute(select(AthleteState))).scalars().all()
    assert states[-1].engine_state["state_update_model"] == STATE_UPDATE_MODEL_VERSION


@pytest.mark.asyncio
async def test_a_shadow_failure_never_breaks_logging_a_workout(async_db, monkeypatch) -> None:
    user = await _user(async_db, "shadow8a_boom@test.com")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("v1 exploded")

    monkeypatch.setattr(svc.dose_engine_v1, "calculate_stress_dose", _boom)

    result = await process_new_workout(async_db, user.id, _log())

    assert result is not None, "ingest must survive a shadow failure"
    rows = (await async_db.execute(select(DoseModelShadowLog))).scalars().all()
    assert rows == []
    logged = (await async_db.execute(select(WorkoutLogORM))).scalars().all()
    assert len(logged) == 1, "the workout itself must still be recorded"
