"""Evaluation metrics shared by the offline ML pipelines.

Byte-identical to the per-pipeline inline computations / private helpers these replace
(``decile_calibration``: q1/q2/q3/q6 ``_decile_calibration``; ``mae`` / ``sign_accuracy``:
the inline regression-gate computations in q1/q2).
"""
from __future__ import annotations

import numpy as np


def mae(pred: np.ndarray, actual: np.ndarray) -> float:
    """Mean absolute error ``mean(|pred - actual|)`` as a float."""
    return float(np.mean(np.abs(np.asarray(pred, dtype=float) - np.asarray(actual, dtype=float))))


def decile_calibration(
    pred: np.ndarray, actual: np.ndarray, *, n_deciles: int = 10
) -> float:
    """Mean |bin-mean prediction - bin-mean actual| across prediction deciles.

    Fewer than ``n_deciles`` points -> NaN. Bins are equal-size splits of the
    prediction-sorted index (``np.array_split`` over ``np.argsort(pred)``).
    """
    if len(pred) < n_deciles:
        return float("nan")
    bins = np.array_split(np.argsort(pred), n_deciles)
    errs = [abs(float(pred[b].mean()) - float(actual[b].mean())) for b in bins if len(b)]
    return float(np.mean(errs))


def sign_accuracy(pred: np.ndarray, actual: np.ndarray, *, eps: float = 1e-9) -> float:
    """Fraction of non-negligible-actual rows where ``sign(pred) == sign(actual)``.

    Rows with ``|actual| <= eps`` are excluded; when none remain, returns 0.0 (matching the
    q1/q2 inline gate behavior).
    """
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    nz = np.abs(actual) > eps
    if not nz.any():
        return 0.0
    return float(np.mean(np.sign(pred[nz]) == np.sign(actual[nz])))
