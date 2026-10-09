"""The one place a set becomes an e1RM estimate (ADR-0056, P4-2b).

Workout extraction, a set reported in Assess and the onboarding seed all call
:func:`estimate_set_e1rm`, so the entry route can never change the number a set produces. The
formula is chosen here, by ``E1RM_CHART_ESTIMATES``; the arithmetic lives in
``app.logic.strength_calibration.estimate_e1rm``.
"""
from __future__ import annotations

from app.core.config import settings
from app.logic import strength_calibration as sc
from app.logic import strength_evidence as se


def estimate_set_e1rm(
    load_kg: float,
    reps: float,
    rpe: float | None,
    rir: float | None,
    effort_fidelity: str = se.FIDELITY_SET_LEVEL,
    *,
    chart_enabled: bool | None = None,
) -> sc.E1rmEstimate:
    """The chart is used only for a set that clears the extraction gate (``is_e1rm_informative``:
    at most 5 reps, near-failure effort) because only such a set can size a prescribed load. A set
    that does not clear it keeps the legacy estimate, so the profile projection and the onboarding
    seed do not jump by the chart's much larger step at high reps or easy efforts.

    ``chart_enabled`` overrides the flag; the activation report passes True to ask what the chart
    would say."""
    enabled = settings.E1RM_CHART_ESTIMATES if chart_enabled is None else chart_enabled
    use_chart = enabled and se.is_e1rm_informative(reps, rpe, rir, effort_fidelity)
    return sc.estimate_e1rm(load_kg=load_kg, reps=reps, rpe=rpe, rir=rir, use_chart=use_chart)
