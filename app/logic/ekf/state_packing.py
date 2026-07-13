"""Pack/unpack between a ``UnifiedStateVector`` and the EKF's 22-dim normalized vector.

The EKF state is ordered ``[X(8) || F(6) || T(8)]`` and normalized per-axis to ~[0, 1]
(each axis divided by its scale: capacity by its ceiling, fatigue/tissue by 100). This
normalized space matches the relative residual semantics of the production scalar
Kalman path (ADR-0034/0036), so covariance seeds and process noise carry over directly.
"""
from __future__ import annotations

import numpy as np

from app.domain.vectors import (
    CapacityState,
    FatigueState,
    TissueState,
    capacity_ceiling,
)
from app.schemas.state import UnifiedStateVector

# Vector attribute on UnifiedStateVector for each domain.
_VECTOR_ATTR = {"capacity": "capacity_x", "fatigue": "fatigue_f", "tissue": "tissue_t"}

# Ordered (domain, key) pairs — the canonical 22-dim state layout.
STATE_KEYS: tuple[tuple[str, str], ...] = (
    *(("capacity", k) for k in CapacityState.KEYS),
    *(("fatigue", k) for k in FatigueState.KEYS),
    *(("tissue", k) for k in TissueState.KEYS),
)
N_STATE: int = len(STATE_KEYS)  # 22

# Index of the first axis of each block (for tests / block-diagonal seeding).
CAPACITY_SLICE = slice(0, len(CapacityState.KEYS))
FATIGUE_SLICE = slice(len(CapacityState.KEYS), len(CapacityState.KEYS) + len(FatigueState.KEYS))
TISSUE_SLICE = slice(len(CapacityState.KEYS) + len(FatigueState.KEYS), N_STATE)

# Domain of each index, and the reverse lookup from (domain, key) -> index.
DOMAIN_OF_INDEX: tuple[str, ...] = tuple(d for d, _ in STATE_KEYS)
INDEX_OF_KEY: dict[tuple[str, str], int] = {dk: i for i, dk in enumerate(STATE_KEYS)}


def axis_scale(domain: str, key: str) -> float:
    """Normalization scale for one axis: capacity -> ceiling, fatigue/tissue -> 100."""
    return capacity_ceiling(key) if domain == "capacity" else 100.0


# Per-axis scale vector (length 22), aligned with STATE_KEYS.
AXIS_SCALE: np.ndarray = np.array([axis_scale(d, k) for d, k in STATE_KEYS], dtype=float)


def pack(state: UnifiedStateVector) -> np.ndarray:
    """Extract the normalized 22-dim state vector from a ``UnifiedStateVector``."""
    raw = np.empty(N_STATE, dtype=float)
    for i, (domain, key) in enumerate(STATE_KEYS):
        sub = getattr(state, _VECTOR_ATTR[domain])
        raw[i] = float(getattr(sub, key))
    return raw / AXIS_SCALE


def unpack(vec: np.ndarray, template: UnifiedStateVector) -> UnifiedStateVector:
    """Rebuild a ``UnifiedStateVector`` from a normalized 22-vec, copying ``template``.

    Auxiliary fields (``s_struct_signal``, legacy mirrors, ``skill_state``) are taken
    from ``template`` unchanged — only the X/F/T axes are overwritten. Values are
    denormalized and clamped to ``[0, scale]`` so downstream math stays in range.
    """
    out = template.model_copy(deep=True)
    denorm = np.clip(np.asarray(vec, dtype=float), 0.0, 1.0) * AXIS_SCALE
    for i, (domain, key) in enumerate(STATE_KEYS):
        sub = getattr(out, _VECTOR_ATTR[domain])
        setattr(sub, key, float(denorm[i]))
    return out
