"""Train Q2 recovery-priors: weak POPULATION priors for the fatigue-clearance modifier.

Fits a REGULARIZED LINEAR model (ridge, alpha chosen by athlete-grouped CV) mapping
z-scored recovery signals -> next-day fatigue-clearance residual, then maps the learned
aggregate population response onto the engine's per-axis beta table and emits the frozen
``q2_recovery_priors_v1`` artifact that ``app.engine.parameter_overrides`` consumes.

Design choices (see ``model_card``):
* Population priors, NOT per-athlete personalization — one aggregate response, reused
  across athletes and axes.
* sleep/stress betas stay at the current engine defaults per axis; only the newly
  learned hrv/rhr terms are added, each scaled to that axis's sleep sensitivity via the
  learned effect *ratio* to sleep. This keeps the change weak and backward-compatible.
* ``shadow_only: true`` — the loader forbids a production caller from applying it.
* NO GBM / deep learning.

Run ``python -m app.ml.q2_recovery.train`` to build -> train -> write
reproducibly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.engine.parameters import default_parameters
from app.ml.common.artifact import write_validated_artifact
from app.ml.common.model_selection import select_alpha_grouped_cv
from app.ml.common.standardize import standardize_label
from app.ml.q2_recovery.build_training_frame import (
    FEATURE_COLUMNS,
    FEATURE_TO_SIGNAL,
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
)
from app.ml.q2_recovery.model_card import MODEL_CARD

ARTIFACT_VERSION = "q2_recovery_priors_v1"
NAMESPACE = "q2_recovery"
CLIP_MIN = 0.60
CLIP_MAX = 1.50

# Map a learned coefficient to a per-axis weight relative to that axis's sleep default.
# Because features are z-scored and the label is standardized to unit variance, a
# coefficient IS a standardized effect size (SD of clearance per 1 SD of the signal).
# _EFFECT_REF is the standardized effect that earns a signal a weight equal to the axis's
# sleep weight; a weak (near-zero) learned effect therefore yields a weak (near-zero)
# beta — the crucial property that keeps synthetic-noise fits from dominating.
_EFFECT_REF = 0.20
# Clamp the dimensionless effect ratio and the resulting per-axis beta to stay weak.
_RATIO_CAP = 1.0
_BETA_CAP = 0.15

_DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "engine"
    / "param_overrides"
    / f"{ARTIFACT_VERSION}.json"
)
_DEFAULT_CSV_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "kaggle"
    / "google-fit-data"
    / "hamon_googlefit_medical_realistic.csv"
)


def fit_population_response(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit the aggregate ridge response; return per-feature coefficients + CV metadata.

    Alpha is selected by athlete-grouped K-fold so the regularization strength is chosen
    without letting an athlete straddle train/validation folds. Features are already
    z-scored; the label is standardized here for coefficient comparability.
    """
    x = frame.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_std, _, _ = standardize_label(frame[LABEL_COLUMN])
    groups = frame[GROUP_COLUMN].to_numpy()

    n_groups = int(np.unique(groups).size)
    alpha = select_alpha_grouped_cv(x, y_std, groups, n_groups)

    model = Ridge(alpha=alpha)
    model.fit(x, y_std)
    coefs = {feat: float(c) for feat, c in zip(FEATURE_COLUMNS, model.coef_, strict=True)}
    return {"alpha": alpha, "coefficients": coefs, "n_rows": int(len(frame)), "n_athletes": n_groups}


def _map_response_to_betas(coefs: dict[str, float]) -> dict[str, dict[str, float]]:
    """Map the aggregate learned response onto the engine's per-axis beta table.

    sleep/stress keep their engine defaults per axis. For each newly learned signal
    (hrv, rhr) we scale that axis's default *sleep* weight by the learned standardized
    effect (``coef_signal / _EFFECT_REF``, clamped), so a bigger-sleep-sensitivity axis
    gets a proportionally larger new term while a near-zero learned effect yields a
    near-zero beta. rhr's effect is naturally negative (lower rhr = better recovery).
    """
    defaults = default_parameters().recovery_clearance_beta

    ratios: dict[str, float] = {}
    for feat, signal in FEATURE_TO_SIGNAL.items():
        if signal == "sleep":
            continue
        r = float(np.clip(coefs.get(feat, 0.0) / _EFFECT_REF, -_RATIO_CAP, _RATIO_CAP))
        ratios[signal] = r

    betas: dict[str, dict[str, float]] = {}
    for axis, sig_weights in defaults.items():
        default_sleep = float(sig_weights.get("sleep", 0.0))
        default_stress = float(sig_weights.get("stress", 0.0))
        axis_betas = {"sleep": round(default_sleep, 4), "stress": round(default_stress, 4)}
        for signal, r in ratios.items():
            beta = float(np.clip(default_sleep * r, -_BETA_CAP, _BETA_CAP))
            axis_betas[signal] = round(beta, 4)
        axis_betas["soreness"] = 0.0  # not present in the source data -> left neutral
        betas[axis] = axis_betas
    return betas


def train(frame: pd.DataFrame, *, source: str = "synthetic:google-fit-csv") -> dict[str, Any]:
    """Train and return the frozen-schema ``q2_recovery_priors_v1`` artifact dict."""
    fit = fit_population_response(frame)
    betas = _map_response_to_betas(fit["coefficients"])
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "recovery_clearance_beta": betas,
        "clip": {"min": CLIP_MIN, "max": CLIP_MAX},
        "training": {
            "model": "ridge",
            "alpha": fit["alpha"],
            "learned_response": fit["coefficients"],
            "n_rows": fit["n_rows"],
            "n_athletes": fit["n_athletes"],
            "label": "next-day fatigue-clearance residual (per-athlete demeaned)",
            "population_priors": True,
            "note": "Synthetic source: SHAPE/weak-prior only. See model_card.MODEL_CARD.",
        },
    }


def holdout_mae(frame: pd.DataFrame, *, holdout_frac: float = 0.25) -> tuple[float, float]:
    """MAE of the learned response vs a neutral (no-recovery-signal) baseline on holdout.

    Athletes are held out as whole groups. The baseline predicts the neutral response
    (0 in standardized-label space = the engine's neutral multiplier, m=1, using no
    recovery signal). Returns ``(mae_learned, mae_baseline)``.
    """
    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    x_tr = train_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_tr, mean, std = standardize_label(train_df[LABEL_COLUMN])
    x_te = test_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_te = (test_df[LABEL_COLUMN].to_numpy(dtype=float) - mean) / std

    model = Ridge(alpha=1.0)
    model.fit(x_tr, y_tr)
    pred = model.predict(x_te)
    mae_learned = float(np.mean(np.abs(y_te - pred)))
    mae_baseline = float(np.mean(np.abs(y_te - 0.0)))  # neutral multiplier / no signal
    return mae_learned, mae_baseline


def write_artifact(artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH) -> Path:
    """Validate against the loader and write the artifact JSON to ``path``."""
    return write_validated_artifact(artifact, path)


def main() -> None:
    frame = build_frame(_DEFAULT_CSV_PATH)
    artifact = train(frame)
    out = write_artifact(artifact)
    mae_learned, mae_baseline = holdout_mae(frame)
    print(MODEL_CARD)
    print(f"\nwrote artifact -> {out}")
    print(f"holdout MAE learned={mae_learned:.4f} baseline={mae_baseline:.4f}")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
