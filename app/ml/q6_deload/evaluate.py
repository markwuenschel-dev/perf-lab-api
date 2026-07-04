"""Offline validation gate for the Q6 deload-need model (mirrors q2_recovery/evaluate).

Decides whether the learned, calibrated P(deload needed in next k days) beats the
production RULE baseline (a score mirroring ``app.logic.deload_need.compute_deload_need``
over the same signals), under the guardrails that gate promotion OUT of shadow: a minimum
AUC improvement, a minimum Brier-score improvement, acceptable calibration (decile
reliability), and no-worse performance for sparse-data athletes. On a no-signal frame the
honest verdict is ``stay_shadow`` — the point of keeping the model shadow-only until real
first-party deload outcomes validate it.

Leakage-clean: the model is fit on held-in athletes and scored on WHOLE held-out athletes
(grouped split), and the feature standardization is fit on the train partition only.
Run ``python -m app.ml.q6_deload.evaluate`` for the current verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.ml.q6_deload.build_training_frame import (
    FEATURE_COLUMNS,
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
    synthetic_deload_rows,
)

# Promotion thresholds — deliberately conservative; a weak prior must clearly help.
MIN_AUC_IMPROVEMENT = 0.02       # AUC_learned - AUC_rule
MIN_BRIER_IMPROVEMENT = 0.003    # Brier_rule - Brier_learned (lower Brier = better)
MAX_CALIBRATION_ERROR = 0.15     # decile reliability of the learned probabilities
SPARSE_TOLERANCE = 0.02          # sparse subgroup Brier may be at most this much worse
SPARSE_OBS_THRESHOLD = 20        # athletes with < this many test rows are "sparse"
_N_DECILES = 10

# Rule-baseline thresholds mirroring app.logic.deload_need.compute_deload_need, adapted to
# the frame's raw signals. Hard drivers are fatigue/tissue levels; soft drivers are the
# accumulating trends + low adherence. Produces a score in [0, 1] used as a probability.
_HARD_FATIGUE = 60.0
_HARD_MEAN_FATIGUE = 45.0
_HARD_TISSUE = 55.0
_SPAN = 40.0  # level units above threshold that saturate a hard driver


@dataclass
class EvalReport:
    n_test_rows: int
    n_test_athletes: int
    positive_rate: float
    auc_rule: float
    auc_learned: float
    auc_improvement: float          # learned - rule (positive = the model helps)
    brier_rule: float
    brier_learned: float
    brier_improvement: float        # rule - learned (positive = the model helps)
    calibration_error: float
    sparse_brier_improvement: float
    verdict: str                    # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rule_baseline_score(frame: pd.DataFrame) -> np.ndarray:
    """Rule-based deload risk in [0, 1], mirroring ``compute_deload_need``'s drivers.

    Hard drivers (single-axis fatigue, mean fatigue, tissue load) each contribute a ramped
    weight once past their threshold; soft drivers (accumulating fatigue/tissue slope,
    worsening performance residual, low adherence) each add a small increment. This is the
    production-equivalent scalar the learned probability must beat.
    """
    fatigue_max = frame["fatigue_max"].to_numpy(dtype=float)
    fatigue_mean = frame["fatigue_mean"].to_numpy(dtype=float)
    tissue_max = frame["tissue_max"].to_numpy(dtype=float)
    score = np.zeros(len(frame))
    score += np.clip((fatigue_max - _HARD_FATIGUE) / _SPAN, 0.0, 1.0) * 0.45
    score += np.clip((fatigue_mean - _HARD_MEAN_FATIGUE) / _SPAN, 0.0, 1.0) * 0.35
    score += np.clip((tissue_max - _HARD_TISSUE) / _SPAN, 0.0, 1.0) * 0.40
    score += (frame["mean_fatigue_slope"].to_numpy(dtype=float) > 0.0) * 0.10
    score += (frame["tissue_slope"].to_numpy(dtype=float) > 0.0) * 0.10
    score += (frame["perf_residual_slope"].to_numpy(dtype=float) > 0.0) * 0.10
    score += (frame["adherence"].to_numpy(dtype=float) < 0.70) * 0.10
    return np.clip(score, 0.0, 1.0)


def _decile_calibration(pred: np.ndarray, actual: np.ndarray) -> float:
    """Mean |bin-mean prediction - bin-mean actual| across prediction deciles."""
    if len(pred) < _N_DECILES:
        return float("nan")
    bins = np.array_split(np.argsort(pred), _N_DECILES)
    errs = [abs(float(pred[b].mean()) - float(actual[b].mean())) for b in bins if len(b)]
    return float(np.mean(errs))


def _fit_predict(train_df: pd.DataFrame, test_df: pd.DataFrame) -> np.ndarray:
    """Standardize on train, fit L2 logistic, return P(deload) on test (leakage-clean)."""
    x_tr = train_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    mean = x_tr.mean(axis=0)
    std = np.where(x_tr.std(axis=0) > 1e-9, x_tr.std(axis=0), 1.0)
    z_tr = (x_tr - mean) / std
    z_te = (test_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float) - mean) / std
    y_tr = train_df[LABEL_COLUMN].to_numpy(dtype=float)
    model = LogisticRegression(C=1.0, max_iter=1000)
    model.fit(z_tr, y_tr)
    return model.predict_proba(z_te)[:, 1]


def evaluate(frame: pd.DataFrame, *, holdout_frac: float = 0.25) -> EvalReport:
    """Fit on held-in athletes, score the held-out athletes, and return the gate report."""
    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    y_te = test_df[LABEL_COLUMN].to_numpy(dtype=float)

    learned = _fit_predict(train_df, test_df)
    rule = rule_baseline_score(test_df)

    single_class = len(np.unique(y_te)) < 2
    auc_learned = 0.5 if single_class else float(roc_auc_score(y_te, learned))
    auc_rule = 0.5 if single_class else float(roc_auc_score(y_te, rule))
    auc_improvement = auc_learned - auc_rule

    brier_learned = float(brier_score_loss(y_te, learned)) if not single_class else float("nan")
    brier_rule = float(brier_score_loss(y_te, rule)) if not single_class else float("nan")
    brier_improvement = brier_rule - brier_learned
    calibration_error = _decile_calibration(learned, y_te)

    counts = test_df.groupby(GROUP_COLUMN).size()
    sparse_ids = set(counts[counts < SPARSE_OBS_THRESHOLD].index.tolist())
    sp = test_df[GROUP_COLUMN].isin(sparse_ids).to_numpy()
    if sp.any() and len(np.unique(y_te[sp])) >= 1:
        sparse_brier_improvement = float(
            brier_score_loss(y_te[sp], rule[sp]) - brier_score_loss(y_te[sp], learned[sp])
        )
    else:
        sparse_brier_improvement = brier_improvement

    reasons: list[str] = []
    if single_class:
        reasons.append("held-out set is single-class; AUC/Brier undefined")
    if auc_improvement < MIN_AUC_IMPROVEMENT:
        reasons.append(f"auc_improvement {auc_improvement:.4f} < {MIN_AUC_IMPROVEMENT}")
    if brier_improvement < MIN_BRIER_IMPROVEMENT:
        reasons.append(f"brier_improvement {brier_improvement:.4f} < {MIN_BRIER_IMPROVEMENT}")
    if not np.isnan(calibration_error) and calibration_error > MAX_CALIBRATION_ERROR:
        reasons.append(f"calibration_error {calibration_error:.3f} > {MAX_CALIBRATION_ERROR}")
    if sparse_brier_improvement < -SPARSE_TOLERANCE:
        reasons.append(f"sparse subgroup worse ({sparse_brier_improvement:.4f})")

    return EvalReport(
        n_test_rows=len(test_df),
        n_test_athletes=int(test_df[GROUP_COLUMN].nunique()),
        positive_rate=round(float(y_te.mean()), 4),
        auc_rule=round(auc_rule, 4),
        auc_learned=round(auc_learned, 4),
        auc_improvement=round(auc_improvement, 4),
        brier_rule=round(brier_rule, 4),
        brier_learned=round(brier_learned, 4),
        brier_improvement=round(brier_improvement, 4),
        calibration_error=round(calibration_error, 4),
        sparse_brier_improvement=round(sparse_brier_improvement, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    frame = build_frame(synthetic_deload_rows())
    report = evaluate(frame)
    print(json.dumps(report.as_dict(), indent=2))
    print(f"\nVERDICT: {report.verdict}")


if __name__ == "__main__":
    main()
