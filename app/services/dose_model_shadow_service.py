"""Compute dose model v1 beside production v0 and record both (phase 8A).

Capture-only. Production state and every recommendation keep using the v0 dose that the
ingest path already computed; this service computes v1 from the SAME log, writes one
``dose_model_shadow_log`` row, and returns nothing. ``decision_impact`` is always
``none_shadow_only``, and a failure here never breaks workout ingestion (the shared
``best_effort_write`` seam, like every other ``*_shadow_service``).

Why now, before phase 8B has data to fit: the re-fit needs to know where v1 differs from v0
and why, and that can only be learned from sessions logged after capture begins. Phases 1.3
and 1.2b ran first so the captured variables carry no known implementation artifacts — no
fabricated set count reaches a v1 dose, and the pre-session state was produced by the
corrected chronology (recorded per row as ``state_update_model``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import dose_engine_v1
from app.logic.state_update_v0 import STATE_UPDATE_MODEL_VERSION
from app.models.dose_model_shadow import DoseModelShadowLog
from app.schemas.prescription import PRESCRIPTION_ENGINE_VERSION
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import ExternalIntensity, StressDose, WorkoutLog
from app.services.telemetry_common import best_effort_write

#: Environment variable a deploy may set to the commit it built. Recorded when present; the
#: current deploy does not set it, so ``code_version`` is None until it does.
BUILD_SHA_ENV = "APP_BUILD_SHA"


def _total(dose: StressDose) -> float:
    return float(sum(dose.dose_six.model_dump().values()))


def _state_before(state: UnifiedStateVector) -> dict[str, Any]:
    """The substrate both doses act on — capacity, fatigue and tissue entering the session."""
    return {
        "timestamp": state.timestamp.isoformat() if state.timestamp else None,
        "capacity": state.capacity_x.model_dump(),
        "fatigue": state.fatigue_f.model_dump(),
        "tissue": state.tissue_t.model_dump(),
        "habit_strength": state.habit_strength,
    }


def build_shadow_row(
    *,
    user_id: int,
    workout_log_id: int | None,
    log: WorkoutLog,
    v0_dose: StressDose,
    v1_dose: StressDose,
    state_before: UnifiedStateVector,
    session_at: datetime,
    planned_domain: str | None = None,
    planned_category: str | None = None,
    n_set_rows: int = 0,
) -> DoseModelShadowLog:
    """Pure construction of one shadow row. Separated so it is testable without a database."""
    v0_total = _total(v0_dose)
    v1_total = _total(v1_dose)
    return DoseModelShadowLog(
        user_id=user_id,
        workout_log_id=workout_log_id,
        # The ingest path passes a naive UTC instant; store it as an explicit UTC instant.
        session_at=(
            session_at.replace(tzinfo=UTC)
            if session_at.tzinfo is None
            else session_at.astimezone(UTC)
        ),
        v0_model_version=v0_dose.dose_model_version or "v0",
        v1_model_version=v1_dose.dose_model_version or "v1",
        state_update_model=STATE_UPDATE_MODEL_VERSION,
        prescription_engine_version=PRESCRIPTION_ENGINE_VERSION,
        code_version=os.environ.get(BUILD_SHA_ENV) or None,
        modality=log.modality,
        planned_domain=planned_domain,
        planned_category=planned_category,
        duration_minutes=log.duration_minutes,
        session_rpe=log.session_rpe,
        reported_sets=log.estimated_sets,
        total_volume_load=log.total_volume_load,
        distance_meters=log.distance_meters,
        n_exercises=len(log.exercises),
        n_set_rows=n_set_rows,
        v0_total=v0_total,
        v1_total=v1_total,
        # Undefined, not infinite, when v0 carried no dose.
        ratio_v1_v0=(v1_total / v0_total) if v0_total > 0.0 else None,
        v0_density_value=v0_dose.density_value,
        v0_density_basis=v0_dose.density_basis,
        v1_density_value=v1_dose.density_value,
        v1_density_basis=v1_dose.density_basis,
        v0_volume_sets_basis=v0_dose.volume_sets_basis,
        v1_volume_sets_basis=v1_dose.volume_sets_basis,
        v1_density_not_modelled=v1_dose.density_basis == "not_applicable",
        v0_volume_used_fabricated_sets=v0_dose.volume_sets_basis == "fabricated_fallback",
        v0_dose_json=v0_dose.model_dump(mode="json"),
        v1_dose_json=v1_dose.model_dump(mode="json"),
        state_before_json=_state_before(state_before),
        decision_impact="none_shadow_only",
    )


async def record_dose_model_shadow(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
    workout_log_id: int | None,
    *,
    v0_dose: StressDose,
    state_before: UnifiedStateVector,
    session_at: datetime,
    external_intensity: ExternalIntensity | None = None,
    planned_domain: str | None = None,
    planned_category: str | None = None,
    n_set_rows: int = 0,
) -> None:
    """Compute v1 for this workout and persist it beside v0 (best-effort, capture-only).

    ``v0_dose`` is the dose production ALREADY computed — passed in, never recomputed, so the
    row compares v1 against exactly what drove the athlete's state.
    """
    async with best_effort_write(
        db, f"dose model shadow (user {user_id}, workout {workout_log_id})"
    ):
        v1_dose = dose_engine_v1.calculate_stress_dose(
            log, external_intensity=external_intensity
        )
        db.add(
            build_shadow_row(
                user_id=user_id,
                workout_log_id=workout_log_id,
                log=log,
                v0_dose=v0_dose,
                v1_dose=v1_dose,
                state_before=state_before,
                session_at=session_at,
                planned_domain=planned_domain,
                planned_category=planned_category,
                n_set_rows=n_set_rows,
            )
        )
