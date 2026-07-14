"""Shared covariance-hygiene assertions for EKF tests (INT-16).

The four existing EKF PSD checks (belief seed, predict, update, shadow-service)
each hand-rolled their own absolute eigenvalue tolerance (-1e-9, -1e-8, -1e-8,
-1e-7), with no shared constant and no rationale for the differences. This
module is the single canonical predicate all of them assert against.

``PSD_REL_TOL`` sits ~5 orders of magnitude above ``eigh``'s actual error floor
(~``norm * 2.2e-16``). Covariance norms in this EKF's normalized [0, 1] state
space are ~1, so ``max(1.0, norm)`` pins ``scale`` at 1 today — the relative
term is a near-inert safety valve for larger norms, by design, not an
oversight.

``is_psd_within_tolerance`` symmetrizes internally, which would LAUNDER a
genuinely asymmetric matrix into a false PSD pass if used alone. It must only
be used paired with a symmetry check — ``assert_covariance_psd`` enforces
that pairing in code rather than relying on caller discipline.
"""
from __future__ import annotations

import numpy as np

PSD_ABS_TOL = 1e-8
PSD_REL_TOL = 1e-10


def _symmetric_part(matrix: np.ndarray) -> np.ndarray:
    return 0.5 * (matrix + matrix.T)


def is_symmetric_within_tolerance(matrix: np.ndarray, *, atol: float = PSD_ABS_TOL) -> bool:
    return bool(np.allclose(matrix, matrix.T, atol=atol))


def is_psd_within_tolerance(
    matrix: np.ndarray, *, abs_tol: float = PSD_ABS_TOL, rel_tol: float = PSD_REL_TOL
) -> bool:
    """Scale-aware PSD predicate. Symmetrizes internally — see module docstring
    for why this must be paired with :func:`is_symmetric_within_tolerance`."""
    symmetric = _symmetric_part(matrix)
    scale = max(1.0, float(np.linalg.norm(symmetric)))
    tolerance = abs_tol + rel_tol * scale
    return bool(np.min(np.linalg.eigvalsh(symmetric)) >= -tolerance)


def assert_covariance_symmetric(matrix: np.ndarray, *, atol: float = PSD_ABS_TOL) -> None:
    max_asymmetry = float(np.max(np.abs(matrix - matrix.T)))
    assert is_symmetric_within_tolerance(matrix, atol=atol), (
        f"covariance is not symmetric within atol={atol}: max asymmetry {max_asymmetry!r}"
    )


def assert_covariance_psd(
    matrix: np.ndarray, *, abs_tol: float = PSD_ABS_TOL, rel_tol: float = PSD_REL_TOL
) -> None:
    """Assert PSD within the scale-aware tolerance.

    Always checks symmetry first (see module docstring) so an asymmetric
    input fails as a symmetry violation, never a laundered PSD pass.
    """
    assert_covariance_symmetric(matrix, atol=abs_tol)
    symmetric = _symmetric_part(matrix)
    min_eig = float(np.min(np.linalg.eigvalsh(symmetric)))
    scale = max(1.0, float(np.linalg.norm(symmetric)))
    tolerance = abs_tol + rel_tol * scale
    assert min_eig >= -tolerance, (
        f"covariance is not PSD within tolerance {tolerance!r}: min eigenvalue {min_eig!r}"
    )
