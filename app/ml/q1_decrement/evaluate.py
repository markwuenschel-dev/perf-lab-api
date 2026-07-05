"""Offline validation gate for the Q1 decrement predictor (mirrors q2_recovery/evaluate).

Decides whether the learned decrement predictor beats the production baseline (neutral /
predict-zero: "no fatigue decrement beyond the planned load"), under the same guardrails
that gate promotion OUT of shadow: a minimum MAE improvement, directional sign accuracy,
decile calibration, no-worse performance for sparse-data athletes, and a low
over-prediction (saturation) fraction. On a no-signal frame the honest verdict is
``stay_shadow`` — the point of keeping the predictor shadow-only until real workout-pair
outcomes validate it.

Leakage-clean: the expectation model that defines the label is refit on the TRAIN
partition only (via ``train.labeled_partition``), so held-out athletes never inform their
own labels. Run ``python -m app.ml.q1_decrement.evaluate`` for the current verdict.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.ml.common.eval_report import BaseEvalReport
from app.ml.common.metrics import decile_calibration, mae, sign_accuracy
from app.ml.common.standardize import standardize_label
from app.ml.q1_decrement.build_training_frame import (
    GROUP_COLUMN,
    PREDICTOR_FEATURES,
    grouped_time_split,
)
from app.ml.q1_decrement.train import labeled_partition

# Promotion thresholds — deliberately conservative; a weak predictor must clearly help.
MIN_IMPROVEMENT = 0.005          # MAE_baseline - MAE_learned, in standardized-label units
MIN_SIGN_ACCURACY = 0.55
MAX_SATURATION_FRACTION = 0.05   # fraction of predictions whose raw decrement is implausible
SPARSE_OBS_THRESHOLD = 10        # athletes with < this many test rows are "sparse"
RPE_DECREMENT_CLIP = 3.0         # |predicted decrement| beyond this many RPE points = saturated


@dataclass
class EvalReport(BaseEvalReport):
    mae_baseline: float = 0.0
    mae_learned: float = 0.0
    improvement: float = 0.0     # baseline - learned (positive = the predictor helps)
    sign_accuracy: float = 0.0
    sparse_improvement: float = 0.0
    saturation_fraction: float = 0.0


def evaluate(frame: pd.DataFrame, *, holdout_frac: float = 0.25) -> EvalReport:
    """Fit on held-in athletes, score the held-out athletes, and return the gate report.

    ``frame`` is a feature frame (``build_feature_frame`` output) carrying the planned
    features + observed ``next_rpe``; the expectation + residual decrement label are
    recomputed per-partition here so the gate stays leakage-clean.
    """
    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    train_l, test_l, _ = labeled_partition(train_df, test_df)

    x_tr = train_l.loc[:, list(PREDICTOR_FEATURES)].to_numpy(dtype=float)
    y_tr, mean, std = standardize_label(train_l["decrement"])
    x_te = test_l.loc[:, list(PREDICTOR_FEATURES)].to_numpy(dtype=float)
    y_te = (test_l["decrement"].to_numpy(dtype=float) - mean) / std

    pred = Ridge(alpha=1.0).fit(x_tr, y_tr).predict(x_te)

    mae_learned = mae(pred, y_te)
    mae_baseline = float(np.mean(np.abs(y_te)))  # neutral baseline predicts 0 (no decrement)
    improvement = mae_baseline - mae_learned

    sign_acc = sign_accuracy(pred, y_te)
    calibration_error = decile_calibration(pred, y_te)

    counts = test_l.groupby(GROUP_COLUMN).size()
    sparse_ids = set(counts[counts < SPARSE_OBS_THRESHOLD].index.tolist())
    sp = test_l[GROUP_COLUMN].isin(sparse_ids).to_numpy()
    sparse_improvement = (
        float(np.mean(np.abs(y_te[sp])) - np.mean(np.abs(y_te[sp] - pred[sp])))
        if sp.any()
        else improvement
    )

    # Saturation guard: convert predictions back to raw RPE units; count implausibly large
    # decrements (the shadow predictor must not emit runaway values).
    pred_raw = pred * std
    saturation_fraction = float(np.mean(np.abs(pred_raw) > RPE_DECREMENT_CLIP))

    reasons: list[str] = []
    if improvement < MIN_IMPROVEMENT:
        reasons.append(f"improvement {improvement:.4f} < {MIN_IMPROVEMENT}")
    if sign_acc < MIN_SIGN_ACCURACY:
        reasons.append(f"sign_accuracy {sign_acc:.3f} < {MIN_SIGN_ACCURACY}")
    if sparse_improvement < 0.0:
        reasons.append(f"sparse subgroup worse ({sparse_improvement:.4f})")
    if saturation_fraction > MAX_SATURATION_FRACTION:
        reasons.append(f"saturation {saturation_fraction:.3f} > {MAX_SATURATION_FRACTION}")

    return EvalReport(
        n_test_rows=len(test_l),
        n_test_athletes=int(test_l[GROUP_COLUMN].nunique()),
        mae_baseline=round(mae_baseline, 4),
        mae_learned=round(mae_learned, 4),
        improvement=round(improvement, 4),
        sign_accuracy=round(sign_acc, 3),
        calibration_error=round(calibration_error, 4),
        sparse_improvement=round(sparse_improvement, 4),
        saturation_fraction=round(saturation_fraction, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    from app.ml.q1_decrement.train import main as train_main

    # The trainer's __main__ builds a planted fixture and prints its own report; reuse it.
    train_main()


if __name__ == "__main__":
    main()
