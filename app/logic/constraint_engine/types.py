"""Constraint engine core types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.schemas.training_goals import TrainingGoal


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class ConstraintResult:
    """Single rule outcome."""

    passed: bool
    severity: Severity
    code: str
    message: str = ""
    insufficient_history: bool = False


@dataclass
class ConstraintContext:
    """Inputs for constraint callables (twin + recent logs)."""

    goal: TrainingGoal
    athlete_state: dict[str, float] = field(default_factory=dict)
    fatigue_state: dict[str, float] = field(default_factory=dict)
    tissue_state: dict[str, float] = field(default_factory=dict)
    skill_state: dict[str, float] = field(default_factory=dict)
    recent_sessions: list[dict[str, Any]] = field(default_factory=list)
    legacy: dict[str, float] = field(default_factory=dict)


ConstraintFn = Callable[[dict[str, Any], ConstraintContext], ConstraintResult]


@dataclass
class ValidationReport:
    """Aggregated constraint validation for one candidate."""

    hard_failed: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    skipped_codes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.hard_failed) == 0
