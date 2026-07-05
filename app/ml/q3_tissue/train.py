"""Train Q3 tissue-risk priors: a calibrated per-axis P(tissue event in next k days).

Fits a REGULARIZED LOGISTIC model (scikit-learn ``LogisticRegression``, L2, inverse
strength ``C`` selected by athlete-grouped K-fold CV) over the tissue-exposure features and
emits a frozen ``shadow_only`` artifact holding the standardization stats + learned
coefficients. The plug-in target is ``TissueRiskPrediction.risk_by_axis`` in
``app.logic.tissue_risk`` — the learned per-axis probability would replace the hand-set,
``calibrated=False`` rule score — but this pipeline DOES NOT wire it: the artifact is
offline/shadow-only and NO override is applied (see ``model_card``). NO gradient boosting,
NO deep learning.

Run ``python -m app.ml.q3_tissue.train`` to build a planted fixture -> train -> print the
artifact + grouped-CV metadata reproducibly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from app.ml.common.artifact import write_artifact as _write_artifact
from app.ml.common.model_selection import select_c_grouped_cv
from app.ml.common.standardize import standardize_columns
from app.ml.q3_tissue.build_training_frame import (
    FEATURE_COLUMNS,
    GROUP_COLUMN,
    HORIZON_DAYS,
    LABEL_COLUMN,
    build_frame,
    synthetic_tissue_rows,
)
from app.ml.q3_tissue.model_card import MODEL_CARD

ARTIFACT_VERSION = "q3_tissue_priors_v1"
NAMESPACE = "q3_tissue"

_DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / f"{ARTIFACT_VERSION}.json"


def fit_tissue_model(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit the L2 logistic model; return coefficients, intercept, scaler stats + CV meta."""
    z, means, stds = standardize_columns(frame, FEATURE_COLUMNS)
    y = frame[LABEL_COLUMN].to_numpy(dtype=float)
    groups = frame[GROUP_COLUMN].to_numpy()
    n_groups = int(np.unique(groups).size)

    c = select_c_grouped_cv(z, y, groups, n_groups)
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
    """Apply a trained artifact -> calibrated per-axis P(tissue event) per row (reproducible)."""
    model = artifact["model"]
    means = model["feature_means"]
    stds = model["feature_stds"]
    coefs = model["coefficients"]
    logit = np.full(len(frame), float(model["intercept"]))
    for feat in FEATURE_COLUMNS:
        z = (frame[feat].to_numpy(dtype=float) - float(means[feat])) / float(stds[feat])
        logit = logit + float(coefs[feat]) * z
    return 1.0 / (1.0 + np.exp(-logit))


def train(frame: pd.DataFrame, *, source: str = "synthetic:planted-tissue-fixture") -> dict[str, Any]:
    """Train and return the ``q3_tissue_priors_v1`` shadow-only artifact dict."""
    fit = fit_tissue_model(frame)
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "horizon_days": HORIZON_DAYS,
        "target": {
            "module": "app.logic.tissue_risk",
            "symbol": "TissueRiskPrediction.risk_by_axis",
            "binding": "learned calibrated per-axis P(tissue event in next k days) would "
            "replace the rule-based, calibrated=False risk_by_axis produced by "
            "compute_tissue_risk; NOT wired — offline/shadow only, no override applied.",
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
            "label": "any tissue event at this axis within the next k days (forward window)",
            "population_priors": True,
            "note": "Synthetic source: SHAPE/weak-prior only. See model_card.MODEL_CARD.",
        },
    }


def write_artifact(artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH) -> Path:
    """Write the shadow-only artifact JSON. No engine loader (Q3 is not wired)."""
    return _write_artifact(artifact, path)


def main() -> None:
    frame = build_frame(synthetic_tissue_rows())
    artifact = train(frame)
    out = write_artifact(artifact)
    print(MODEL_CARD)
    print(f"\nwrote artifact -> {out}")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
