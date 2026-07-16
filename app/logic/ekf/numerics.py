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

    The rescale is the congruence ``D P D`` with ``D = diag(sqrt(d_new / d))`` a
    *non-negative* diagonal matrix, so it preserves positive-semi-definiteness:
    ``xᵀ (D P D) x = (Dx)ᵀ P (Dx) >= 0`` for any PSD ``P``. That is what lets
    :func:`stabilize` clamp *after* projecting to the PSD cone (see its docstring).
    """
    d = np.clip(np.diag(P), 1e-12, None)
    d_new = np.clip(d, lo, hi)
    scale = np.sqrt(d_new / d)
    Ps = P * np.outer(scale, scale)
    return symmetrize(Ps)


def stabilize(P: np.ndarray, lo: np.ndarray, hi: np.ndarray, floor: float = 1e-9) -> np.ndarray:
    """Symmetrize, project to PSD, then clamp the diagonal into ``[lo, hi]``.

    Order matters, and this order is load-bearing (INT-16). Projecting to the PSD
    cone *last* would undo the clamp: ``nearest_psd`` is a global eigen-reconstruction
    that can push diagonal entries back outside ``[lo, hi]`` — unboundedly far, not
    merely by float noise. Clamping last instead yields both invariants at once:

    * **diagonal in [lo, hi]** — exactly, by construction: ``clip_diagonal`` sets
      ``diag`` to ``d_new = clip(d, lo, hi)``.
    * **PSD** — because ``clip_diagonal`` is a congruence by a non-negative diagonal
      matrix, which cannot introduce a negative eigenvalue (see its docstring).

    ``nearest_psd`` leaves every eigenvalue ``>= floor``, hence every diagonal entry
    ``P[i, i] = e_iᵀ P e_i >= floor``, so ``clip_diagonal``'s internal ``1e-12`` guard
    never binds here and the rescale factors stay well conditioned.

    The trade this order makes, stated honestly: the *output* eigenvalues are no longer
    guaranteed ``>= floor``. Clamping shrinks eigenvalues by up to ``max(d_new / d)``,
    so a variance clamped hard downward can leave a minimum eigenvalue below ``floor``
    (still ``>= 0`` up to float roundoff). The two guarantees are mutually exclusive in
    general: for a perfectly correlated ``P = [[1, 1], [1, 1]]`` with ``lo = hi = 1``,
    every correlation-preserving matrix meeting the band is singular. PSD-ness plus an
    exact band is the pair the callers need — neither call site factorizes ``P``.
    """
    return clip_diagonal(nearest_psd(symmetrize(P), floor=floor), lo, hi)
