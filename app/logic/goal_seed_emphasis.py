"""Goal-aware baseline capacity emphasis (twin-seed quality).

The baseline seed was goal-blind: `primary_goal` was persisted but never reached the
seeder, so a Runner, a Powerlifter and a Hypertrophy athlete all seeded identically from
experience level alone. This tilts the *unmeasured* axes toward the athlete's domain — a
specialist shouldn't start pinned near zero on their own specialty.

It is a **floor on un-measured axes only**: it never lowers a value and never overrides an
axis backed by a real input (a supplied squat/5K). The emphasized axes stay
`experience_prior` tier (ADR-0059) — a domain prior is still uncertain, just less wrong.
"""

from __future__ import annotations

from app.domain.vectors import CapacityState
from app.logic.domain_vocab import DOMAINS, GOAL_TO_DOMAIN, canonical_domain

# Per-domain minimum starting values for the axes that domain trains. Strength-family
# axes are on the 0–100 scale; `aerobic` is on the engine's 0–650 scale.
GOAL_AXIS_FLOOR: dict[str, dict[str, float]] = {
    "powerlifting": {"max_strength": 35.0, "hypertrophy": 30.0},
    "weightlifting": {"max_strength": 35.0, "power": 42.0},
    "strength": {"max_strength": 32.0},
    "hypertrophy": {"hypertrophy": 42.0, "max_strength": 26.0},
    "power": {"power": 45.0, "max_strength": 28.0},
    "running": {"aerobic": 360.0, "work_capacity": 42.0},
    "gymnastics": {"skill": 58.0, "power": 38.0, "mobility": 58.0},
    "calisthenics": {"skill": 55.0, "max_strength": 26.0},
    "grip": {"max_strength": 26.0},
    "mixed": {"work_capacity": 46.0, "glycolytic": 46.0, "aerobic": 330.0},
    "general": {},
}


def domain_for_goal(goal: str | None) -> str | None:
    """Canonical training domain for a goal/domain string, or None if unrecognized."""
    if not goal:
        return None
    resolved = GOAL_TO_DOMAIN.get(goal) or canonical_domain(goal)
    return resolved if resolved in DOMAINS else None


def apply_goal_emphasis(
    capacity: CapacityState, goal: str | None, measured_axes: set[str]
) -> None:
    """Raise the goal domain's axes to their floor, in place — skipping any axis backed
    by a real measurement and never lowering a value."""
    domain = domain_for_goal(goal)
    if domain is None:
        return
    for axis, floor in GOAL_AXIS_FLOOR.get(domain, {}).items():
        if axis in measured_axes:
            continue
        if float(getattr(capacity, axis)) < floor:
            setattr(capacity, axis, floor)
