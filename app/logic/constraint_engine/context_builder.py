"""Build ConstraintContext from UnifiedStateVector + recent session dicts."""

from __future__ import annotations

from typing import Any

from app.logic.constraint_engine.types import ConstraintContext
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


def build_constraint_context(
    state: UnifiedStateVector,
    recent_sessions: list[dict[str, Any]] | None,
    goal: TrainingGoal,
) -> ConstraintContext:
    cap = state.capacity_x
    fat = state.fatigue_f
    tis = state.tissue_t

    athlete_state = {
        "aerobic": cap.aerobic,
        "glycolytic": cap.glycolytic,
        "max_strength": cap.max_strength,
        "hypertrophy": cap.hypertrophy,
        "power": cap.power,
        "skill": cap.skill,
        "mobility": cap.mobility,
        "work_capacity": cap.work_capacity,
        "habit_strength": state.habit_strength,
    }
    fatigue_state = {k: getattr(fat, k) for k in fat.KEYS}
    tissue_state = {k: getattr(tis, k) for k in tis.KEYS}
    # INT-05 C4: rules read the decomposed vectors above, never the legacy scalar mirror.
    # ``s_struct_signal`` is its own state (not a mirror), so it stays here.
    legacy = {"s_struct_signal": state.s_struct_signal}

    return ConstraintContext(
        goal=goal,
        athlete_state=athlete_state,
        fatigue_state=fatigue_state,
        tissue_state=tissue_state,
        skill_state=dict(state.skill_state),
        recent_sessions=list(recent_sessions or []),
        legacy=legacy,
    )
