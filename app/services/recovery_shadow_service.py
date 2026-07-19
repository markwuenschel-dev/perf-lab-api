"""Recovery shadow service (Q2 recovery priors, Rail 3).

For a wellness ingest, compute the baseline (production defaults) vs learned (shadow
override) fatigue-clearance multipliers for the athlete's current fatigue state and
record them in ``recovery_shadow_log``. Applies NOTHING to production state — this is
capture-only. Best-effort: a failure here must never break wellness ingest.

The learned prior is loaded via the override loader with ``allow_shadow=True`` (the only
caller permitted to apply a ``shadow_only`` artifact), keeping learned values off every
production decision path until validated and promoted.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import FatigueState
from app.engine.parameter_overrides import apply_parameter_overrides, load_namespace_override
from app.engine.parameters import default_parameters
from app.logic.recovery_telemetry import multipliers_by_axis, wellness_snapshot
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.recovery_shadow import RecoveryShadowLog
from app.services.state_service import load_current_state
from app.services.telemetry_common import best_effort_write

_NAMESPACE = "q2_recovery"


async def record_recovery_shadow(
    db: AsyncSession, user_id: int, snapshot: WellnessTelemetrySnapshot
) -> None:
    """Write one recovery shadow-telemetry row. Never raises to the caller.

    Takes an immutable snapshot, never a live WellnessSample ORM instance (AUD-C24)."""
    async with best_effort_write(db, f"recovery shadow log for user {user_id}"):
        params = default_parameters()
        artifact = load_namespace_override(_NAMESPACE)
        if artifact is not None:
            learned = apply_parameter_overrides(params, artifact, allow_shadow=True)
            model_version = str(artifact["version"])
        else:
            learned = params
            model_version = "none"

        state = await load_current_state(db, user_id)
        fatigue_before = (
            {a: round(float(getattr(state.fatigue_f, a)), 2) for a in FatigueState.KEYS}
            if state is not None
            else {}
        )

        db.add(
            RecoveryShadowLog(
                user_id=user_id,
                model_version=model_version,
                wellness=wellness_snapshot(snapshot),
                fatigue_before=fatigue_before,
                baseline_clearance_multiplier=multipliers_by_axis(params, snapshot),
                learned_clearance_multiplier=multipliers_by_axis(learned, snapshot),
                decision_impact="none_shadow_only",
            )
        )
