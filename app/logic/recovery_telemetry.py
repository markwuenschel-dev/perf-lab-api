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

# (beta_key, wellness_field, direction, baseline, norm) — mirrors
# readiness_service._SIGNAL_CONFIG. The recovery beta "stress" signal has no wellness
# field (production reads it from the workout log, not a check-in), so it is absent here;
# with the default params (sleep+stress only) the shadow baseline reduces to the sleep term.
_WELLNESS_Z: tuple[tuple[str, str, float, float, float], ...] = (
    ("sleep", "sleep_hours", 1.0, 8.0, 2.0),
    ("hrv", "hrv_ms", 1.0, 60.0, 20.0),
    ("rhr", "resting_hr", -1.0, 55.0, 10.0),
    ("soreness", "soreness", -1.0, 3.0, 3.0),
    ("mood", "mood", 1.0, 6.0, 3.0),
)


def _signal(wellness: Any, field: str) -> float | None:
    v = getattr(wellness, field, None)
    return None if v is None else float(v)


def clearance_multiplier(params: EngineParameters, axis: str, wellness: Any) -> float:
    """Fatigue-clearance multiplier for one axis given a wellness sample. Signals absent
    from either the params' beta map or the sample are skipped (contribute nothing)."""
    beta = params.recovery_clearance_beta.get(axis, {})
    z_clip = params.recovery_zscore_scale
    total = 0.0
    for key, field, direction, base, norm in _WELLNESS_Z:
        w = beta.get(key)
        if w is None:
            continue
        val = _signal(wellness, field)
        if val is None:
            continue
        z = direction * (val - base) / norm
        z = max(-z_clip, min(z_clip, z))
        total += w * z
    return max(params.recovery_clearance_min, min(params.recovery_clearance_max, math.exp(total)))


def multipliers_by_axis(params: EngineParameters, wellness: Any) -> dict[str, float]:
    """Per-axis clearance multipliers (rounded) for all six fatigue axes."""
    return {axis: round(clearance_multiplier(params, axis, wellness), 4) for axis in FatigueState.KEYS}


def wellness_snapshot(wellness: Any) -> dict[str, float | None]:
    """The wellness signals used by the multiplier, for the telemetry row."""
    return {field: _signal(wellness, field) for _, field, *_ in _WELLNESS_Z}
