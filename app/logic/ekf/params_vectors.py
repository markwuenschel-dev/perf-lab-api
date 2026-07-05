"""Derive 22-length EKF vectors (process noise, variance bounds) from EngineParameters.

Kept separate so both ``belief`` and ``transition`` can import without a cycle. All
values are in normalized per-axis variance space, aligned with ``state_packing.STATE_KEYS``.
"""
from __future__ import annotations

import numpy as np

from app.engine.parameters import EngineParameters

from .state_packing import STATE_KEYS


def process_noise_vector(params: EngineParameters) -> np.ndarray:
    """Per-axis process noise Q (variance/day), length 22, aligned with STATE_KEYS."""
    out = np.empty(len(STATE_KEYS), dtype=float)
    for i, (domain, key) in enumerate(STATE_KEYS):
        if domain == "capacity":
            out[i] = float(params.confidence_process_noise_per_day.get(key, 0.0025))
        elif domain == "fatigue":
            out[i] = float(params.fatigue_process_noise_per_day.get(key, 0.02))
        else:  # tissue
            out[i] = float(params.tissue_process_noise_per_day.get(key, 0.01))
    return out


def variance_bounds(params: EngineParameters) -> tuple[np.ndarray, np.ndarray]:
    """Per-axis (lo, hi) variance clamps, length 22 each, aligned with STATE_KEYS."""
    lo = np.empty(len(STATE_KEYS), dtype=float)
    hi = np.empty(len(STATE_KEYS), dtype=float)
    for i, (domain, key) in enumerate(STATE_KEYS):
        if domain == "capacity":
            lo[i] = float(params.confidence_min_variance.get(key, 0.01))
            hi[i] = float(params.confidence_max_variance.get(key, 1.5))
        else:  # fatigue / tissue
            lo[i] = float(params.ekf_min_variance)
            hi[i] = float(params.ekf_max_variance)
    return lo, hi


def seed_variance_vector(params: EngineParameters, capacity_variances: dict[str, float]) -> np.ndarray:
    """Block-diagonal seed variance, length 22.

    Capacity axes seed from the supplied production ``capacity_confidence`` values (the
    best available prior); fatigue/tissue from the flat EKF seed constants.
    """
    out = np.empty(len(STATE_KEYS), dtype=float)
    for i, (domain, key) in enumerate(STATE_KEYS):
        if domain == "capacity":
            out[i] = float(capacity_variances.get(key, 1.0))
        elif domain == "fatigue":
            out[i] = float(params.ekf_seed_variance_fatigue)
        else:  # tissue
            out[i] = float(params.ekf_seed_variance_tissue)
    return out
