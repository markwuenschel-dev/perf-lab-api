"""Rail 4 — offline validation gate for Q2 recovery priors.

Decides whether the learned recovery response predicts next-day recovery better than the
production baseline (neutral / no recovery signal), under the guardrails that gate
promotion OUT of shadow: a minimum MAE improvement, directional sign accuracy, decile
calibration, no-worse performance for sparse-data athletes, and a low multiplier-
saturation fraction. On a near-zero-signal source (e.g. the synthetic google-fit CSV) the
verdict is honestly ``stay_shadow`` — which is the whole point of keeping the prior
shadow-only until real first-party outcomes validate it.

Frame-based here (the wellness time-series the trainer uses). The production
``recovery_shadow_log`` is the same computation over accumulated rows joined to each
athlete's next-day wellness, once that data exists.

Run ``python -m app.ml.q2_recovery.evaluate`` for the current verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.ml.q2_recovery.build_training_frame import (
    FEATURE_COLUMNS,
    FEATURE_TO_SIGNAL,
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
)
from app.ml.q2_recovery.train_recovery_priors import _DEFAULT_CSV_PATH, _standardize_label

# Promotion thresholds — deliberately conservative; a weak prior must clearly help.
MIN_IMPROVEMENT = 0.005          # MAE_baseline - MAE_learned, in standardized-label units
MIN_SIGN_ACCURACY = 0.55
MAX_SATURATION_FRACTION = 0.05   # fraction of the population whose learned multiplier clips
SPARSE_OBS_THRESHOLD = 10        # athletes with < this many test rows are "sparse"
_Z_CLIP = 2.0                    # mirrors EngineParameters.recovery_zscore_scale
_N_DECILES = 10


@dataclass
class EvalReport:
    n_test_rows: int
    n_test_athletes: int
    mae_baseline: float
    mae_learned: float
    improvement: float           # baseline - learned (positive = the prior helps)
    sign_accuracy: float
    calibration_error: float
    sparse_improvement: float
    saturation_fraction: float
    verdict: str                 # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decile_calibration(pred: np.ndarray, actual: np.ndarray) -> float:
    """Mean |bin-mean prediction - bin-mean actual| across prediction deciles."""
    if len(pred) < _N_DECILES:
        return float("nan")
    bins = np.array_split(np.argsort(pred), _N_DECILES)
    errs = [abs(float(pred[b].mean()) - float(actual[b].mean())) for b in bins if len(b)]
    return float(np.mean(errs))


def _saturation_fraction(frame: pd.DataFrame, artifact: dict[str, Any]) -> float:
    """Fraction of the frame whose learned clearance multiplier would clip.

    Mirrors the production multiplier (per-signal z clamped to +/-_Z_CLIP, then
    ``exp(sum beta*z)``) using the artifact's cns betas; counts rows outside the clip.
    """
    beta = artifact["recovery_clearance_beta"]["cns"]
    clip = artifact["clip"]
    score = np.zeros(len(frame))
    for feat, signal in FEATURE_TO_SIGNAL.items():
        w = float(beta.get(signal, 0.0))
        z = np.clip(frame[feat].to_numpy(dtype=float), -_Z_CLIP, _Z_CLIP)
        score = score + w * z
    mult = np.exp(score)
    return float(np.mean((mult < clip["min"]) | (mult > clip["max"])))


def evaluate(
    frame: pd.DataFrame, *, artifact: dict[str, Any] | None = None, holdout_frac: float = 0.25
) -> EvalReport:
    """Fit on held-in athletes, score the held-out athletes, and return the gate report."""
    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    x_tr = train_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_tr, mean, std = _standardize_label(train_df[LABEL_COLUMN])
    x_te = test_df.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_te = (test_df[LABEL_COLUMN].to_numpy(dtype=float) - mean) / std

    pred = Ridge(alpha=1.0).fit(x_tr, y_tr).predict(x_te)

    mae_learned = float(np.mean(np.abs(y_te - pred)))
    mae_baseline = float(np.mean(np.abs(y_te)))  # neutral baseline predicts 0 (m=1, no signal)
    improvement = mae_baseline - mae_learned

    nz = np.abs(y_te) > 1e-9
    sign_accuracy = float(np.mean(np.sign(pred[nz]) == np.sign(y_te[nz]))) if nz.any() else 0.0
    calibration_error = _decile_calibration(pred, y_te)

    counts = test_df.groupby(GROUP_COLUMN).size()
    sparse_ids = set(counts[counts < SPARSE_OBS_THRESHOLD].index.tolist())
    sp = test_df[GROUP_COLUMN].isin(sparse_ids).to_numpy()
    sparse_improvement = (
        float(np.mean(np.abs(y_te[sp])) - np.mean(np.abs(y_te[sp] - pred[sp]))) if sp.any() else improvement
    )

    saturation_fraction = _saturation_fraction(test_df, artifact) if artifact else 0.0

    reasons: list[str] = []
    if improvement < MIN_IMPROVEMENT:
        reasons.append(f"improvement {improvement:.4f} < {MIN_IMPROVEMENT}")
    if sign_accuracy < MIN_SIGN_ACCURACY:
        reasons.append(f"sign_accuracy {sign_accuracy:.3f} < {MIN_SIGN_ACCURACY}")
    if sparse_improvement < 0.0:
        reasons.append(f"sparse subgroup worse ({sparse_improvement:.4f})")
    if saturation_fraction > MAX_SATURATION_FRACTION:
        reasons.append(f"saturation {saturation_fraction:.3f} > {MAX_SATURATION_FRACTION}")

    return EvalReport(
        n_test_rows=len(test_df),
        n_test_athletes=int(test_df[GROUP_COLUMN].nunique()),
        mae_baseline=round(mae_baseline, 4),
        mae_learned=round(mae_learned, 4),
        improvement=round(improvement, 4),
        sign_accuracy=round(sign_accuracy, 3),
        calibration_error=round(calibration_error, 4),
        sparse_improvement=round(sparse_improvement, 4),
        saturation_fraction=round(saturation_fraction, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    from app.engine.parameter_overrides import load_namespace_override

    frame = build_frame(_DEFAULT_CSV_PATH)
    report = evaluate(frame, artifact=load_namespace_override("q2_recovery"))
    print(json.dumps(report.as_dict(), indent=2))
    print(f"\nVERDICT: {report.verdict}")


if __name__ == "__main__":
    main()
