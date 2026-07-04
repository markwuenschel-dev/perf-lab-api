"""Train Q6 deload-need priors: a calibrated P(deload needed in next k days).

Fits a REGULARIZED LOGISTIC model (scikit-learn ``LogisticRegression``, L2, inverse
strength ``C`` selected by athlete-grouped K-fold CV) over the deload-risk features and
emits a frozen ``shadow_only`` artifact holding the standardization stats + learned
coefficients. The plug-in target is ``DeloadNeed.score`` in ``app.logic.deload_need`` —
the learned probability would augment/replace the hand-set rule score — but this pipeline
DOES NOT wire it: the artifact is offline/shadow only (see ``model_card``). NO gradient
boosting, NO deep learning.

Run ``python -m app.ml.q6_deload.train`` to build a planted fixture -> train -> print the
artifact + grouped-CV AUC reproducibly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from app.ml.q6_deload.build_training_frame import (
    FEATURE_COLUMNS,
    GROUP_COLUMN,
    HORIZON_DAYS,
    LABEL_COLUMN,
    build_frame,
    synthetic_deload_rows,
)
from app.ml.q6_deload.model_card import MODEL_CARD

ARTIFACT_VERSION = "q6_deload_priors_v1"
NAMESPACE = "q6_deload"

# Candidate L2 inverse-regularization strengths for grouped CV (smaller C = stronger
# regularization). The grid stays on the strong side to keep a weak, calibrated prior.
_C_GRID: tuple[float, ...] = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)

_DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / f"{ARTIFACT_VERSION}.json"


def _standardize_columns(frame: pd.DataFrame) -> tuple[np.ndarray, dict[str, float], dict[str, float]]:
    """Return standardized feature matrix + per-feature mean/std (std=0 -> 1.0)."""
    x = frame.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std > 1e-9, std, 1.0)
    z = (x - mean) / std
    means = {f: float(m) for f, m in zip(FEATURE_COLUMNS, mean, strict=True)}
    stds = {f: float(s) for f, s in zip(FEATURE_COLUMNS, std, strict=True)}
    return z, means, stds


def _select_c_grouped_cv(x: np.ndarray, y: np.ndarray, groups: np.ndarray, n_groups: int) -> float:
    """Pick ``C`` by athlete-grouped K-fold CV (max mean validation AUC).

    Grouped folds keep an athlete from straddling train/validation. Folds where the
    validation slice is single-class (AUC undefined) are skipped.
    """
    if n_groups < 2:
        return 1.0
    cv = GroupKFold(n_splits=min(5, n_groups))
    best_c, best_auc = 1.0, -np.inf
    for c in _C_GRID:
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


def fit_deload_model(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit the L2 logistic model; return coefficients, intercept, scaler stats + CV meta."""
    z, means, stds = _standardize_columns(frame)
    y = frame[LABEL_COLUMN].to_numpy(dtype=float)
    groups = frame[GROUP_COLUMN].to_numpy()
    n_groups = int(np.unique(groups).size)

    c = _select_c_grouped_cv(z, y, groups, n_groups)
    model = LogisticRegression(C=c, max_iter=1000)
    model.fit(z, y)
    coefs = {f: float(w) for f, w in zip(FEATURE_COLUMNS, model.coef_[0], strict=True)}
    return {
        "C": c,
        "coefficients": coefs,
        "intercept": float(model.intercept_[0]),
        "feature_means": means,
        "feature_stds": stds,
        "n_rows": int(len(frame)),
        "n_athletes": n_groups,
        "base_rate": float(y.mean()),
    }


def predict_proba(frame: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    """Apply a trained artifact -> calibrated P(deload needed) per row (reproducible)."""
    model = artifact["model"]
    means = model["feature_means"]
    stds = model["feature_stds"]
    coefs = model["coefficients"]
    logit = np.full(len(frame), float(model["intercept"]))
    for feat in FEATURE_COLUMNS:
        z = (frame[feat].to_numpy(dtype=float) - float(means[feat])) / float(stds[feat])
        logit = logit + float(coefs[feat]) * z
    return 1.0 / (1.0 + np.exp(-logit))


def train(frame: pd.DataFrame, *, source: str = "synthetic:planted-deload-fixture") -> dict[str, Any]:
    """Train and return the ``q6_deload_priors_v1`` shadow-only artifact dict."""
    fit = fit_deload_model(frame)
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "horizon_days": HORIZON_DAYS,
        "target": {
            "module": "app.logic.deload_need",
            "symbol": "DeloadNeed.score",
            "binding": "learned P(deload needed in next k days) would augment/replace the "
            "rule-based score; NOT wired — offline/shadow only.",
        },
        "features": list(FEATURE_COLUMNS),
        "model": {
            "type": "logistic_regression",
            "penalty": "l2",
            "C": fit["C"],
            "intercept": fit["intercept"],
            "coefficients": fit["coefficients"],
            "feature_means": fit["feature_means"],
            "feature_stds": fit["feature_stds"],
        },
        "training": {
            "n_rows": fit["n_rows"],
            "n_athletes": fit["n_athletes"],
            "base_rate": fit["base_rate"],
            "label": "any deload event within the next k days (forward window)",
            "population_priors": True,
            "note": "Synthetic source: SHAPE/weak-prior only. See model_card.MODEL_CARD.",
        },
    }


def write_artifact(artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH) -> Path:
    """Write the shadow-only artifact JSON. No engine loader (Q6 is not wired)."""
    out = Path(path)
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    frame = build_frame(synthetic_deload_rows())
    artifact = train(frame)
    out = write_artifact(artifact)
    print(MODEL_CARD)
    print(f"\nwrote artifact -> {out}")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
