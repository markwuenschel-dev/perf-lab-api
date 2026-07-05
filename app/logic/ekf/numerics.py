"""Small covariance-hygiene helpers for the shadow EKF.

Covariances must stay symmetric and positive-semi-definite (PSD) through many
predict/update steps. These utilities enforce that cheaply and deterministically.
"""
from __future__ import annotations

import numpy as np


def symmetrize(P: np.ndarray) -> np.ndarray:
    """Return the symmetric part ``(P + Pᵀ) / 2`` — removes numerical asymmetry."""
    return 0.5 * (P + P.T)


def nearest_psd(P: np.ndarray, floor: float = 1e-9) -> np.ndarray:
    """Project a symmetric matrix onto the PSD cone by flooring its eigenvalues.

    Uses the symmetric eigendecomposition (``eigh``); eigenvalues below ``floor`` are
    raised to ``floor``. Cheap at 22x22 and robust to the small negative eigenvalues
    that finite-difference Jacobians and repeated Joseph updates can introduce.
    """
    Ps = symmetrize(P)
    vals, vecs = np.linalg.eigh(Ps)
    vals = np.clip(vals, floor, None)
    return symmetrize((vecs * vals) @ vecs.T)


def clip_diagonal(P: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Clamp per-axis variances (the diagonal) into ``[lo, hi]``, preserving correlations.

    Rescales the corresponding rows/cols so off-diagonal covariances stay consistent
    with the clamped variances (keeps correlation coefficients unchanged when a variance
    is capped). Returns a symmetric matrix.
    """
    d = np.clip(np.diag(P), 1e-12, None)
    d_new = np.clip(d, lo, hi)
    scale = np.sqrt(d_new / d)
    Ps = P * np.outer(scale, scale)
    return symmetrize(Ps)


def stabilize(P: np.ndarray, lo: np.ndarray, hi: np.ndarray, floor: float = 1e-9) -> np.ndarray:
    """Symmetrize, clamp diagonal into ``[lo, hi]``, then project to PSD."""
    return nearest_psd(clip_diagonal(symmetrize(P), lo, hi), floor=floor)
