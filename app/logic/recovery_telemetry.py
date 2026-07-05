"""Pure recovery-shadow telemetry helpers (Q2 recovery priors, Rail 3).

Computes the fatigue-clearance multiplier ``m_a(z) = clip(exp(Σ_k β_{a,k}·z_k), min, max)``
from a wellness sample under a given ``EngineParameters``, using the readiness per-signal
z-score convention. Kept pure / DB-free so the baseline-vs-learned comparison is
unit-testable. Nothing here touches production state — this is the shadow computation only.
"""
from __future__ import annotations

import math
from typing import Any

from app.domain.vectors import FatigueState
from app.engine.parameters import EngineParameters
from app.logic.wellness_signals import SIGNAL_CONFIG

# Recovery-clearance beta signal key -> wellness field name. The per-field z-score
# parameters (direction, baseline, norm) come from the shared SIGNAL_CONFIG, so this can
# no longer drift from the readiness convention. The recovery beta uses a subset of the
# readiness signals (no sleep_quality) under its own key names. The "stress" beta signal
# has no wellness field (production reads it from the workout log, not a check-in), so it
# is absent here; with the default params (sleep+stress) the baseline reduces to sleep.
_BETA_KEY_FIELD: dict[str, str] = {
    "sleep": "sleep_hours",
    "hrv": "hrv_ms",
    "rhr": "resting_hr",
    "soreness": "soreness",
    "mood": "mood",
}


def _signal(wellness: Any, field: str) -> float | None:
    v = getattr(wellness, field, None)
    return None if v is None else float(v)


def clearance_multiplier(params: EngineParameters, axis: str, wellness: Any) -> float:
    """Fatigue-clearance multiplier for one axis given a wellness sample. Signals absent
    from either the params' beta map or the sample are skipped (contribute nothing)."""
    beta = params.recovery_clearance_beta.get(axis, {})
    z_clip = params.recovery_zscore_scale
    total = 0.0
    for key, field in _BETA_KEY_FIELD.items():
        w = beta.get(key)
        if w is None:
            continue
        val = _signal(wellness, field)
        if val is None:
            continue
        direction, base, norm = SIGNAL_CONFIG[field]
        z = direction * (val - base) / norm
        z = max(-z_clip, min(z_clip, z))
        total += w * z
    return max(params.recovery_clearance_min, min(params.recovery_clearance_max, math.exp(total)))


def multipliers_by_axis(params: EngineParameters, wellness: Any) -> dict[str, float]:
    """Per-axis clearance multipliers (rounded) for all six fatigue axes."""
    return {axis: round(clearance_multiplier(params, axis, wellness), 4) for axis in FatigueState.KEYS}


def wellness_snapshot(wellness: Any) -> dict[str, float | None]:
    """The wellness signals used by the multiplier, for the telemetry row."""
    return {field: _signal(wellness, field) for field in _BETA_KEY_FIELD.values()}
