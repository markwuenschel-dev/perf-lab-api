"""Constraint registry + session validation + scoring helpers."""

from app.logic.constraint_engine.candidates import encode_session_candidate
from app.logic.constraint_engine.context_builder import build_constraint_context
from app.logic.constraint_engine.scoring import simple_session_scorer
from app.logic.constraint_engine.types import (
    ConstraintContext,
    ConstraintResult,
    Severity,
    ValidationReport,
)
from app.logic.constraint_engine.validator import SessionValidator

__all__ = [
    "ConstraintContext",
    "ConstraintResult",
    "Severity",
    "ValidationReport",
    "SessionValidator",
    "build_constraint_context",
    "encode_session_candidate",
    "simple_session_scorer",
]
