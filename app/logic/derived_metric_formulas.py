"""
Custom derived KPI calculators (formula_type == custom_python_key).

Context keys are resolved by the dashboard service (benchmark codes, nested KPIs,
profile fields).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

MILE_PER_400M = 1609.34 / 400.0


def hinshaw_fatigue_factor(ctx: Mapping[str, Any]) -> float:
    """
    Hinshaw-style 400m→mile gap: positive % means mile pace is slower than
    speed-endurance extrapolation from 400m (aerobic / durability deficit proxy).
    """
    t400 = float(ctx["run_400m_time"])
    t_mile = float(ctx["run_1mile_time"])
    if t400 <= 0 or t_mile <= 0:
        return 0.0
    predicted_mile = t400 * MILE_PER_400M
    return 100.0 * (t_mile / predicted_mile - 1.0)


def relative_total(ctx: Mapping[str, Any]) -> float:
    total = float(ctx["pl_projected_total"])
    bw = float(ctx.get("bodyweight_kg") or 0.0)
    if bw < 40.0:
        bw = 75.0
    return total / bw


def pull_support_balance(ctx: Mapping[str, Any]) -> float:
    """Simple balance score: pull strength vs isometric support."""
    pulls = max(0.0, float(ctx["gym_strict_pullup_max"]))
    hold_s = max(0.0, float(ctx["gym_ring_support_hold"]))
    denom = max(1.0, hold_s / 30.0)
    return pulls / denom


CUSTOM_FORMULAS: dict[str, Callable[[Mapping[str, Any]], float]] = {
    "hinshaw_fatigue_factor": hinshaw_fatigue_factor,
    "relative_total": relative_total,
    "pull_support_balance": pull_support_balance,
}
