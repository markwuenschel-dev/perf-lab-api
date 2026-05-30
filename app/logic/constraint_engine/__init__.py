"""
Constraint Engine package.

This package owns:
- Structured constraint validation against coaching templates (`SessionValidator`)
- Candidate representation and scoring for the main prescriber (`SessionCandidate`, `score_candidate`)
- Context building utilities

There are currently two related but distinct regimes:
1. Main prescriber candidate flow (prescriber.py) — uses SessionCandidate + score_candidate
2. Template-driven coaching constraints — uses SessionValidator + CONSTRAINT_REGISTRY

Long-term these should converge further.
"""

from app.logic.constraint_engine.candidate import (
    SessionCandidate,
    score_candidate,
    apply_block_context_boost,
    mean_fatigue,
    max_tissue_load,
    overall_readiness,
)
from app.logic.constraint_engine.context_builder import build_constraint_context
from app.logic.constraint_engine.types import (
    ConstraintContext,
    ConstraintResult,
    Severity,
    ValidationReport,
)
from app.logic.constraint_engine.validator import SessionValidator

# Legacy re-exports (will be cleaned up later)
from app.logic.constraint_engine.scoring import simple_session_scorer  # noqa: F401

__all__ = [
    "SessionCandidate",
    "score_candidate",
    "apply_block_context_boost",
    "mean_fatigue",
    "max_tissue_load",
    "overall_readiness",
    "ConstraintContext",
    "ConstraintResult",
    "Severity",
    "ValidationReport",
    "SessionValidator",
    "build_constraint_context",
    "simple_session_scorer",
]
