"""Rail 4 — offline promotion gate for the dose-law calibration prior.

Decides whether the CALIBRATED dose weights predict the next-session outcome proxy better
than the current production (DEFAULT) weights, under guardrails that gate promotion OUT of
shadow: a minimum held-out MAE improvement, no-worse performance for sparse-data athletes,
and a low nudge-saturation fraction (a weak prior must not lean on the clamp caps). On a
near-zero-signal source the verdict is honestly ``stay_shadow`` — which is the whole point
of keeping the prior shadow-only until real first-party dose outcomes validate it.

Run ``python -m app.ml.dose_calibration.evaluate`` for the current verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.engine.parameters import default_parameters
from app.ml.common.standardize import standardize_label
from app.ml.dose_calibration.build_training_frame import (
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
    modeled_doses,
    synthesize_sessions,
)
from app.ml.dose_calibration.train import (
    _MAX_SHAPE_NUDGE,
    _MAX_WEIGHT_NUDGE,
    train,
)

# Promotion thresholds — deliberately conservative; a weak prior must clearly help.
MIN_IMPROVEMENT = 0.005          # MAE_default - MAE_calibrated, in standardized-label units
SPARSE_OBS_THRESHOLD = 10        # athletes with < this many test rows are "sparse"
MAX_SATURATION_FRACTION = 0.34   # fraction of emitted dose nudges allowed to sit at the clamp cap
_CLAMP_TOL = 1e-6


@dataclass
class EvalReport:
    n_test_rows: int
    n_test_athletes: int
    mae_default: float
    mae_calibrated: float
    improvement: float           # default - calibrated (positive = the prior helps)
    sparse_improvement: float
    saturation_fraction: float
    verdict: str                 # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _saturation_fraction(artifact: dict[str, Any]) -> float:
    """Fraction of the artifact's dose nudges sitting at their clamp boundary."""
    p = default_parameters()
    eo = artifact.get("engine_overrides", {})
    at_cap = 0
    total = 0
    for name, cap in (("dose_volume_weights", _MAX_WEIGHT_NUDGE),
                      ("dose_shape_six_by_modality", _MAX_SHAPE_NUDGE)):
        block = eo.get(name)
        if not block:
            continue
        if name == "dose_volume_weights":
            defaults = p.dose_volume_weights
            pairs = [(defaults[k], v) for k, v in block.items()]
        else:
            defaults_nested = p.dose_shape_six_by_modality
            pairs = [
                (defaults_nested[m][ax], v)
                for m, axes in block.items()
                for ax, v in axes.items()
            ]
        for base, val in pairs:
            total += 1
            factor = val / base if base else 1.0
            if abs(abs(factor - 1.0) - cap) <= _CLAMP_TOL:
                at_cap += 1
    return float(at_cap / total) if total else 0.0


def _row_abs_errors(
    train_df: pd.DataFrame, test_df: pd.DataFrame, params: Any
) -> tuple[np.ndarray, np.ndarray]:
    """Per-row |error| of the 1-D dose->outcome map (fit on train) on train and test."""
    y_tr, y_mean, y_std = standardize_label(train_df[LABEL_COLUMN])
    y_te = (test_df[LABEL_COLUMN].to_numpy(dtype=float) - y_mean) / y_std
    d_tr_raw = modeled_doses(train_df, params)
    d_te_raw = modeled_doses(test_df, params)
    d_std, d_mean, d_sd = standardize_label(d_tr_raw)
    d_te = (d_te_raw - d_mean) / d_sd
    model = Ridge(alpha=1.0).fit(d_std.reshape(-1, 1), y_tr)
    pred = model.predict(d_te.reshape(-1, 1))
    return np.abs(y_te - pred), y_te


def evaluate(
    frame: pd.DataFrame, *, artifact: dict[str, Any] | None = None, holdout_frac: float = 0.25
) -> EvalReport:
    """Fit on held-in athletes, score the held-out athletes, and return the gate report."""
    from app.engine.parameter_overrides import apply_dose_overrides

    if artifact is None:
        artifact = train(frame)

    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    default_p = default_parameters()
    calibrated_p = apply_dose_overrides(default_p, artifact, allow_shadow=True)

    err_cal, _ = _row_abs_errors(train_df, test_df, calibrated_p)
    err_def, _ = _row_abs_errors(train_df, test_df, default_p)

    mae_calibrated = float(np.mean(err_cal))
    mae_default = float(np.mean(err_def))
    improvement = mae_default - mae_calibrated

    counts = test_df.groupby(GROUP_COLUMN).size()
    sparse_ids = set(counts[counts < SPARSE_OBS_THRESHOLD].index.tolist())
    sp = test_df[GROUP_COLUMN].isin(sparse_ids).to_numpy()
    sparse_improvement = (
        float(np.mean(err_def[sp]) - np.mean(err_cal[sp])) if sp.any() else improvement
    )

    saturation_fraction = _saturation_fraction(artifact)

    reasons: list[str] = []
    if improvement < MIN_IMPROVEMENT:
        reasons.append(f"improvement {improvement:.4f} < {MIN_IMPROVEMENT}")
    if sparse_improvement < 0.0:
        reasons.append(f"sparse subgroup worse ({sparse_improvement:.4f})")
    if saturation_fraction > MAX_SATURATION_FRACTION:
        reasons.append(f"saturation {saturation_fraction:.3f} > {MAX_SATURATION_FRACTION}")

    return EvalReport(
        n_test_rows=len(test_df),
        n_test_athletes=int(test_df[GROUP_COLUMN].nunique()),
        mae_default=round(mae_default, 4),
        mae_calibrated=round(mae_calibrated, 4),
        improvement=round(improvement, 4),
        sparse_improvement=round(sparse_improvement, 4),
        saturation_fraction=round(saturation_fraction, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    # Self-contained: evaluate a freshly trained prior on a deterministic synthetic frame.
    frame = build_frame(synthesize_sessions())
    artifact = train(frame)
    report = evaluate(frame, artifact=artifact)
    print(json.dumps(report.as_dict(), indent=2))
    print(f"\nVERDICT: {report.verdict}")


if __name__ == "__main__":
    main()
