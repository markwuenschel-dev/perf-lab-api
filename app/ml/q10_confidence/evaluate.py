"""Rail 4 — offline calibration gate for Q10 confidence process-noise.

Decides whether the learned per-axis process-noise makes the model's PREDICTED capacity
variance track the OBSERVED squared residual better than the engine default — under
guardrails that gate promotion OUT of shadow. The learned ``q`` is fit on held-in
athletes; calibration is measured on held-out athletes (grouped split), so a spurious
slope from pure measurement noise does not earn promotion.

Predicted variance for a pair mirrors the engine (ADR-0036):

    predicted_var = prior_var + q · elapsed_days

with ``prior_var`` the axis's measurement-noise floor (the train intercept ≈ 2·R, shared
by the learned and default evaluations). Calibration is a reliability diagram: bin the
holdout pairs by predicted variance and average |mean predicted − mean observed| across
bins.

Promotion requires (ALL):
  * enough holdout pairs,
  * a genuine positive elapsed-days signal — the fit slope is significant
    (t ≥ MIN_SLOPE_T) and the learned noise is meaningfully positive
    (≥ MIN_LEARNED_NOISE); this is what fails honestly on a no-signal source,
  * the learned noise calibrates better than the default by ≥ MIN_CALIB_IMPROVEMENT,
  * every learned noise stays within sane bounds.

Frame-based here (synthetic, Postgres-free). The production feed is the same computation
over ``app.analysis.feature_builders.confidence_calibration_features`` once real
benchmark sequences accumulate.

Run ``python -m app.ml.q10_confidence.evaluate`` for the current verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.ml.q10_confidence.build_training_frame import (
    AXIS_COLUMN,
    FEATURE_COLUMN,
    GROUP_COLUMN,
    TARGET_COLUMN,
    build_frame,
    grouped_split,
    synthesize_observations,
)
from app.ml.q10_confidence.train import (
    default_process_noise,
    fit_process_noise,
)

# Promotion thresholds — deliberately conservative; a weak/noisy fit must not promote.
MIN_HOLDOUT_PAIRS = 60
MIN_SLOPE_T = 3.0            # in-sample significance of the elapsed-days slope
MIN_LEARNED_NOISE = 0.002   # learned q must be meaningfully positive (vs ~0 on noise)
MIN_CALIB_IMPROVEMENT = 0.002  # reliability-error reduction vs default, [0,1]^2 units
PROCESS_NOISE_ABS_MAX = 0.5
_N_BINS = 10
_VAR_FLOOR = 1e-4           # keep prior_var strictly positive if an intercept goes <= 0


@dataclass
class EvalReport:
    n_test_pairs: int
    n_test_athletes: int
    n_axes: int
    learned_noise_mean: float
    default_noise_mean: float
    median_slope_t: float
    median_learned_noise: float
    calib_error_default: float
    calib_error_learned: float
    calib_improvement: float          # default - learned (positive = learned helps)
    per_axis: dict[str, dict[str, float]]
    verdict: str                      # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reliability_error(pred_var: np.ndarray, observed_sq: np.ndarray) -> float:
    """Mean |bin-mean predicted var − bin-mean observed squared-residual| over bins."""
    n = pred_var.size
    if n == 0:
        return float("nan")
    n_bins = min(_N_BINS, n)
    order = np.argsort(pred_var)
    errs = [
        abs(float(pred_var[b].mean()) - float(observed_sq[b].mean()))
        for b in np.array_split(order, n_bins)
        if b.size
    ]
    return float(np.mean(errs))


def _predicted_variance(
    test_df: pd.DataFrame, noise_by_axis: dict[str, float], prior_var_by_axis: dict[str, float]
) -> np.ndarray:
    """predicted_var = prior_var[axis] + q[axis] · elapsed_days, per holdout pair."""
    elapsed = test_df[FEATURE_COLUMN].to_numpy(dtype=float)
    axes = test_df[AXIS_COLUMN].to_numpy()
    q = np.array([noise_by_axis.get(str(a), 0.0) for a in axes], dtype=float)
    prior = np.array([prior_var_by_axis.get(str(a), _VAR_FLOOR) for a in axes], dtype=float)
    return prior + q * elapsed


def evaluate(frame: pd.DataFrame, *, holdout_frac: float = 0.25) -> EvalReport:
    """Fit process-noise on held-in athletes; score calibration on the held-out ones."""
    train_df, test_df = grouped_split(frame, holdout_frac=holdout_frac)
    fits = fit_process_noise(train_df)
    defaults = default_process_noise()

    learned_noise = {a: float(f["learned_process_noise"]) for a, f in fits.items()}
    default_noise = {a: float(defaults.get(a, 0.0025)) for a in fits}
    # Shared per-axis measurement-noise floor from the train intercept (≈ 2·R).
    prior_var = {a: max(_VAR_FLOOR, float(f["intercept"])) for a, f in fits.items()}

    observed_sq = test_df[TARGET_COLUMN].to_numpy(dtype=float)
    pred_learned = _predicted_variance(test_df, learned_noise, prior_var)
    pred_default = _predicted_variance(test_df, default_noise, prior_var)
    calib_learned = _reliability_error(pred_learned, observed_sq)
    calib_default = _reliability_error(pred_default, observed_sq)
    calib_improvement = calib_default - calib_learned

    slope_ts = [float(f["slope_t"]) for f in fits.values()]
    learned_vals = list(learned_noise.values())
    median_slope_t = float(np.median(slope_ts)) if slope_ts else 0.0
    median_learned_noise = float(np.median(learned_vals)) if learned_vals else 0.0

    per_axis: dict[str, dict[str, float]] = {}
    test_counts = test_df.groupby(AXIS_COLUMN).size().to_dict()
    for axis, f in fits.items():
        per_axis[axis] = {
            "learned_noise": round(learned_noise[axis], 6),
            "default_noise": round(default_noise[axis], 6),
            "slope_t": round(float(f["slope_t"]), 3),
            "prior_var": round(prior_var[axis], 6),
            "n_test_pairs": int(test_counts.get(axis, 0)),
        }

    n_test_pairs = int(len(test_df))
    reasons: list[str] = []
    if n_test_pairs < MIN_HOLDOUT_PAIRS:
        reasons.append(f"holdout pairs {n_test_pairs} < {MIN_HOLDOUT_PAIRS}")
    if median_slope_t < MIN_SLOPE_T:
        reasons.append(f"slope signal weak (median t {median_slope_t:.2f} < {MIN_SLOPE_T})")
    if median_learned_noise < MIN_LEARNED_NOISE:
        reasons.append(
            f"learned noise ~0 (median {median_learned_noise:.5f} < {MIN_LEARNED_NOISE})"
        )
    if not (calib_improvement >= MIN_CALIB_IMPROVEMENT):
        reasons.append(f"calibration gain {calib_improvement:.4f} < {MIN_CALIB_IMPROVEMENT}")
    if any(v > PROCESS_NOISE_ABS_MAX for v in learned_vals):
        reasons.append(f"a learned noise exceeds bound {PROCESS_NOISE_ABS_MAX}")

    return EvalReport(
        n_test_pairs=n_test_pairs,
        n_test_athletes=int(test_df[GROUP_COLUMN].nunique()),
        n_axes=len(fits),
        learned_noise_mean=round(float(np.mean(learned_vals)) if learned_vals else 0.0, 6),
        default_noise_mean=round(
            float(np.mean(list(default_noise.values()))) if default_noise else 0.0, 6
        ),
        median_slope_t=round(median_slope_t, 3),
        median_learned_noise=round(median_learned_noise, 6),
        calib_error_default=round(calib_default, 5),
        calib_error_learned=round(calib_learned, 5),
        calib_improvement=round(calib_improvement, 5),
        per_axis=per_axis,
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    # Postgres-free: a planted-signal synthetic fixture stands in for the DB feed.
    frame = build_frame(synthesize_observations(process_noise=0.01, seed=7))
    report = evaluate(frame)
    print(json.dumps(report.as_dict(), indent=2))
    print(f"\nVERDICT: {report.verdict}")


if __name__ == "__main__":
    main()
