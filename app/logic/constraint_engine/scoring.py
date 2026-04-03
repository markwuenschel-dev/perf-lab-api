"""Template-aligned session scoring (goal_bias vs twin capacities)."""

from __future__ import annotations

from typing import Any

from app.schemas.coaching_template import StructuredCoachingTemplate
from app.schemas.state import UnifiedStateVector


def _norm_cap(v: float, cap: float = 400.0) -> float:
    return max(0.0, min(1.0, v / cap))


def simple_session_scorer(
    candidate: dict[str, Any],
    template: StructuredCoachingTemplate,
    state: UnifiedStateVector,
) -> float:
    """
    Dot goal_bias with normalized twin proxies minus mild penalties from candidate cost proxies.
    Returns ~0..1 (not strictly bounded if penalties stack).
    """
    gb = template.goal_bias
    cap = state.capacity_x
    # Map goal_bias keys to capacity / legacy fields
    strength = _norm_cap(cap.max_strength, 120.0)
    power = _norm_cap(cap.power, 80.0)
    hypertrophy = _norm_cap(cap.hypertrophy, 80.0)
    aerobic = _norm_cap(cap.aerobic, 400.0)
    glycolytic = _norm_cap(cap.glycolytic, 80.0)
    skill = _norm_cap(cap.skill, 80.0)
    tissue_resilience = max(
        0.0,
        min(
            1.0,
            1.0 - (state.tissue_t.wrist + state.tissue_t.elbow + state.tissue_t.lumbar) / 300.0,
        ),
    )

    align = (
        gb.strength * strength
        + gb.power * power
        + gb.hypertrophy * hypertrophy
        + gb.aerobic * aerobic
        + gb.glycolytic * glycolytic
        + gb.skill * skill
        + gb.tissue_resilience * tissue_resilience
    )
    denom = (
        gb.strength
        + gb.power
        + gb.hypertrophy
        + gb.aerobic
        + gb.glycolytic
        + gb.skill
        + gb.tissue_resilience
    )
    base = align / denom if denom > 0 else 0.5

    cns_pen = float(candidate.get("estimated_cns_cost", 0.5)) * (state.fatigue_f.cns / 100.0) * 0.15
    tissue_pen = float(candidate.get("tissue_cost", 0.5)) * (state.fatigue_f.structural / 100.0) * 0.1

    score = base - cns_pen - tissue_pen
    return max(0.0, min(1.0, round(score, 4)))
