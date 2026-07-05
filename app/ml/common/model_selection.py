"""Grouped cross-validation hyperparameter selection for the offline ML pipelines.

Ridge alpha (min mean validation MSE) and logistic C (max mean validation AUC) selected by
athlete/group-grouped K-fold so no group straddles a fold. Byte-identical to the per-pipeline
copies these replace (Ridge: q1/q2/dose_calibration; logistic: q3/q6).
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import mean_squared_error, roc_auc_score
from sklearn.model_selection import GroupKFold

# Candidate ridge strengths for grouped CV (q1/q2/dose_calibration).
DEFAULT_ALPHAS: tuple[float, ...] = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0)
# Candidate L2 inverse-regularization strengths for logistic grouped CV (q3/q6);
# smaller C = stronger regularization.
DEFAULT_C_GRID: tuple[float, ...] = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)


def select_alpha_grouped_cv(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    n_groups: int,
    *,
    alphas: tuple[float, ...] = DEFAULT_ALPHAS,
) -> float:
    """Pick the ridge alpha by athlete-grouped K-fold CV (min mean validation MSE)."""
    if n_groups < 2:
        return 1.0
    cv = GroupKFold(n_splits=min(5, n_groups))
    best_alpha, best_mse = 1.0, np.inf
    for alpha in alphas:
        fold_mse: list[float] = []
        for tr_idx, va_idx in cv.split(x, y, groups):
            model = Ridge(alpha=alpha)
            model.fit(x[tr_idx], y[tr_idx])
            fold_mse.append(float(mean_squared_error(y[va_idx], model.predict(x[va_idx]))))
        mean_mse = float(np.mean(fold_mse))
        if mean_mse < best_mse:
            best_alpha, best_mse = alpha, mean_mse
    return best_alpha


def select_c_grouped_cv(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    n_groups: int,
    *,
    c_grid: tuple[float, ...] = DEFAULT_C_GRID,
) -> float:
    """Pick ``C`` by athlete-grouped K-fold CV (max mean validation AUC).

    Grouped folds keep a group (all its rows) from straddling train/validation. Folds
    whose validation slice is single-class (AUC undefined) are skipped.
    """
    if n_groups < 2:
        return 1.0
    cv = GroupKFold(n_splits=min(5, n_groups))
    best_c, best_auc = 1.0, -np.inf
    for c in c_grid:
        fold_aucs: list[float] = []
        for tr_idx, va_idx in cv.split(x, y, groups):
            if len(np.unique(y[va_idx])) < 2:
                continue
            model = LogisticRegression(C=c, max_iter=1000)
            model.fit(x[tr_idx], y[tr_idx])
            proba = model.predict_proba(x[va_idx])[:, 1]
            fold_aucs.append(float(roc_auc_score(y[va_idx], proba)))
        if not fold_aucs:
            continue
        mean_auc = float(np.mean(fold_aucs))
        if mean_auc > best_auc:
            best_c, best_auc = c, mean_auc
    return best_c
