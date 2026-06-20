"""
Domain layer for the Performance Lab engine.

This package contains the core mathematical and conceptual models
that are independent of API schemas, ORM models, and specific
persistence concerns.

Current scope (initial cut):
- Vector primitives (Capacity, Fatigue, Tissue, Dose, Adaptation signals)
- These are the building blocks described in PROJECT_AGENT_BRIEF.md

Longer term this package should own:
- State transition rules
- Dose-response mathematics
- Cross-talk matrices
- Versioned state snapshots
"""

from app.domain.vectors import (
    AdaptationContribution,
    CapacityState,
    EnergyMix,
    FatigueState,
    PhiVectors,
    StressDoseSix,
    TissueState,
)

__all__ = [
    "CapacityState",
    "FatigueState",
    "TissueState",
    "StressDoseSix",
    "AdaptationContribution",
    "PhiVectors",
    "EnergyMix",
]
