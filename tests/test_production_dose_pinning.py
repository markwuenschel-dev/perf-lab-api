"""Production dose is pinned by construction, not by declaration.

The defect this file exists for: after #230 the registry (app/logic/dose_model.py) declared
production pinned to v0, and ``select_production_dose_model`` refused to return v1 — while
``app/services/state_service.py`` imported ``calculate_stress_dose`` straight from
``dose_engine_v1``. Workout ingestion, the path that builds athlete state, computed with the
uncalibrated model. Nothing noticed, because nothing read the registry: every caller named an
engine module directly, and the registry test only asserted what the registry said.

It never reached production — EC2 ran 1143467, which predates #230 — but ``main`` was wrong
for three merges.

So the pin is now checked where it can actually fail:

* **Structurally**: no module outside the declared shadow/replay/calibration paths may import a
  versioned engine's ``calculate_stress_dose``. Live callers go through ``app.logic.dose_engine``.
* **At the seam**: a real workout ingested through ``process_new_workout`` must persist a dose
  stamped with the production model's version. That test is what caught the original bug.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

APP = Path(__file__).resolve().parents[1] / "app"

#: Modules allowed to import a VERSIONED engine directly, each for a stated reason. Anything
#: else must use app.logic.dose_engine. Extending this list is a reviewed decision.
PINNED_IMPORTERS: dict[str, str] = {
    "app/logic/dose_engine.py": "the production entry point itself",
    "app/logic/dose_engine_v1.py": "v1 reuses the v0 law through the DoseVariables seam",
    "app/logic/dose_routing.py": "ADR-0054 shadow routing, calibrated against v0 bases",
    "app/logic/dose_routing_calibration.py": "calibrates the v0 routing split",
    "app/ml/dose_calibration/build_training_frame.py": "the fitted artifact is v0-fitted",
    "app/ml/q10_confidence/ekf_replay.py": "replays STORED history under the engine that made it",
    "app/services/dose_model_shadow_service.py": "8A: computes v1 beside v0, never applied",
    "app/scripts/compare_dose_models.py": "offline v0/v1 comparison matrix",
    "app/scripts/simulate_matrix.py": "offline phase-1 matrix; compares v0/v1 to explain a finding",
    "app/scripts/compare_difficulty_policies.py": "offline 3.2 evidence; reports candidate policies under both engines",
    "app/scripts/compare_difficulty_live_path.py": "offline 3.4 evidence; candidate vs the live rule under both engines",
    # Helpers that are not dose semantics (external-intensity building, set samples).
    "app/services/state_service.py": "imports v0 intensity HELPERS only — never the dose law",
}

VERSIONED_ENGINES = ("app.logic.dose_engine_v0", "app.logic.dose_engine_v1")


def _imports_of(path: Path) -> list[tuple[str, list[str]]]:
    """(module, imported names) for every import statement in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, []))
        # `from app.logic import dose_engine_v1`
        if isinstance(node, ast.ImportFrom) and node.module == "app.logic":
            for alias in node.names:
                if alias.name in ("dose_engine_v0", "dose_engine_v1"):
                    found.append((f"app.logic.{alias.name}", ["<module>"]))
    return found


def _app_modules():
    for path in APP.rglob("*.py"):
        yield path.relative_to(APP.parent).as_posix(), path


def test_no_live_caller_imports_a_versioned_dose_law_directly() -> None:
    """The check that would have caught state_service importing v1."""
    offenders: list[str] = []
    for rel, path in _app_modules():
        for module, names in _imports_of(path):
            if module not in VERSIONED_ENGINES:
                continue
            takes_the_law = "calculate_stress_dose" in names or "<module>" in names
            if not takes_the_law:
                continue  # helpers (intensity builders, set samples) are not dose semantics
            if rel not in PINNED_IMPORTERS:
                offenders.append(f"{rel} imports the dose law from {module}")

    assert not offenders, (
        "live dose callers must use app.logic.dose_engine, not a versioned engine:\n"
        + "\n".join(offenders)
    )


def test_state_service_takes_only_helpers_from_a_versioned_engine() -> None:
    """state_service is allowlisted for HELPERS; it must never take the law itself."""
    path = APP / "services" / "state_service.py"
    for module, names in _imports_of(path):
        if module in VERSIONED_ENGINES:
            assert "calculate_stress_dose" not in names, (
                f"state_service takes the dose law from {module} — use app.logic.dose_engine"
            )


def test_the_production_entry_point_resolves_through_the_registry() -> None:
    from app.logic import dose_engine
    from app.logic.dose_model import PRODUCTION_DOSE_MODEL

    assert dose_engine.PRODUCTION_DOSE_MODEL_NAME == PRODUCTION_DOSE_MODEL


def test_a_production_dose_is_stamped_with_the_production_model() -> None:
    from datetime import UTC, datetime

    from app.logic.dose_engine import PRODUCTION_DOSE_MODEL_NAME, calculate_stress_dose
    from app.schemas.workouts import WorkoutLog

    dose = calculate_stress_dose(
        WorkoutLog(
            timestamp=datetime(2026, 9, 19, tzinfo=UTC),
            modality="Strength",
            duration_minutes=60.0,
            session_rpe=7.0,
            estimated_sets=20.0,
        )
    )

    assert dose.dose_model_version == PRODUCTION_DOSE_MODEL_NAME == "v0"


# ── the seam ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_ingested_workout_persists_a_production_model_dose(async_db) -> None:
    """Through the real ingest path — the only test here that would have failed on #230's main.

    The registry tests all passed while ingestion computed v1, because they asserted what the
    registry SAID. This one asserts what the state was actually BUILT from.
    """
    from datetime import UTC, datetime

    from app.logic.dose_model import PRODUCTION_DOSE_MODEL
    from app.models.user import User
    from app.models.workout_log import WorkoutLog as WorkoutLogORM
    from app.schemas.workouts import WorkoutLog
    from app.services.state_service import process_new_workout

    user = User(email="pinning@test.com", hashed_password="x", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)

    await process_new_workout(
        async_db,
        user.id,
        WorkoutLog(
            timestamp=datetime(2026, 9, 19, 9, 0, tzinfo=UTC),
            modality="Strength",
            duration_minutes=60.0,
            session_rpe=7.5,
            estimated_sets=20.0,
            total_volume_load=8000.0,
        ),
    )

    logged = (await async_db.execute(select(WorkoutLogORM))).scalars().one()
    assert logged.dose_snapshot["dose_model_version"] == PRODUCTION_DOSE_MODEL, (
        "the dose that built this athlete's state came from "
        f"{logged.dose_snapshot['dose_model_version']!r}, not the production model"
    )
