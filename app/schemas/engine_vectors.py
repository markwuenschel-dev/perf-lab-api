"""
Typed vectors for the full-spectrum engine.

These are now re-exported from the domain layer (app/domain/vectors.py),
which is the canonical source of truth for the mathematical model.

This module is kept as a backward-compatible shim so existing imports
(`from app.schemas.engine_vectors import ...`) continue to resolve to the
*same* class objects as the domain layer. New internal engine code should
prefer importing directly from:

    from app.domain.vectors import CapacityState, ...
"""

from __future__ import annotations

# Re-export everything from the domain layer so existing imports continue to
# work AND resolve to the same class objects (no shadowing / duplicate models).
from app.domain.vectors import (  # noqa: F401
    SEED_CAPACITY_VARIANCE,
    AdaptationContribution,
    CapacityConfidence,
    CapacityKey,
    CapacityState,
    EnergyMix,
    FatigueKey,
    FatigueState,
    PhiVectors,
    StressDoseSix,
    TissueKey,
    TissueState,
)

__all__ = [
    "SEED_CAPACITY_VARIANCE",
    "AdaptationContribution",
    "CapacityConfidence",
    "CapacityKey",
    "CapacityState",
    "EnergyMix",
    "FatigueKey",
    "FatigueState",
    "PhiVectors",
    "StressDoseSix",
    "TissueKey",
    "TissueState",
]
