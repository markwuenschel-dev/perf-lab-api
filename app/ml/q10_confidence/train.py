"""Train Q10 confidence-calibration: per-axis capacity process-noise (shadow-only).

Fits, per capacity axis, the ordinary-least-squares line

    squared_residual = intercept + slope · elapsed_days

over consecutive benchmark-observation pairs. Because the pair residual is the
innovation of a random-walk-plus-noise process
(``E[(y2 - y1)^2 | dt] = q·dt + 2·R``), the SLOPE is a method-of-moments estimate of the
per-day process-noise ``q`` (``EngineParameters.confidence_process_noise_per_day``) and
the INTERCEPT ≈ ``2·R`` recovers the measurement-noise floor (compare against
``EngineParameters.confidence_measured_variance``).

Emits a versioned, ``shadow_only`` artifact recording learned-vs-default per-axis noise
plus an UNWIRED ``target`` binding to ``EngineParameters.confidence_process_noise_per_day``.
Nothing here applies an override: this pipeline may only touch ``app/ml/q10_confidence``,
and per ADR-0036 the process-noise is unwired from any override loader — the artifact
documents the binding for a future promotion, it does not perform it.

Run ``python -m app.ml.q10_confidence.train`` to build → fit → write reproducibly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.engine.parameters import default_parameters
from app.ml.q10_confidence.build_training_frame import (
    AXIS_COLUMN,
    FEATURE_COLUMN,
    TARGET_COLUMN,
    build_frame,
    synthesize_observations,
)
from app.ml.q10_confidence.model_card import MODEL_CARD

ARTIFACT_VERSION = "q10_confidence_calibration_v1"
NAMESPACE = "q10_confidence"
TARGET_PARAMETER = "EngineParameters.confidence_process_noise_per_day"

# Sanity bounds on a learned per-day process-noise (in [0, 1]^2 variance units). A fit
# outside this is treated as degenerate and is clamped for the artifact record.
PROCESS_NOISE_MIN = 0.0
PROCESS_NOISE_MAX = 0.5

_DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parent / "artifacts" / f"{ARTIFACT_VERSION}.json"
)


def default_process_noise() -> dict[str, float]:
    """Current engine per-axis process-noise defaults (read-only, ADR-0036)."""
    return dict(default_parameters().confidence_process_noise_per_day)


def default_measured_variance() -> float:
    """Engine full-weight measurement variance R (for the intercept ≈ 2·R check)."""
    return float(default_parameters().confidence_measured_variance)


def fit_axis(elapsed: np.ndarray, squared_residual: np.ndarray) -> dict[str, Any]:
    """OLS fit of ``squared_residual ~ intercept + slope·elapsed`` for one axis.

    Returns the learned process-noise (``slope``, clamped to sane bounds for the record
    but reported raw too), the measurement-noise intercept, the implied measurement
    variance (intercept / 2), and the slope t-statistic used downstream as the
    signal-significance guardrail. A degenerate fit (too few pairs or no variation in
    elapsed days) yields ``learned = 0`` with ``t = 0``.
    """
    x = np.asarray(elapsed, dtype=float)
    y = np.asarray(squared_residual, dtype=float)
    n = int(x.size)
    if n < 3 or float(np.var(x)) < 1e-12:
        return {
            "learned_process_noise": 0.0,
            "learned_process_noise_raw": 0.0,
            "intercept": float(np.mean(y)) if n else 0.0,
            "implied_measurement_variance": (float(np.mean(y)) / 2.0) if n else 0.0,
            "slope_se": float("inf"),
            "slope_t": 0.0,
            "n_pairs": n,
        }
    design = np.column_stack([np.ones(n), x])
    xtx = design.T @ design
    xtx_inv = np.linalg.inv(xtx)
    beta = xtx_inv @ (design.T @ y)
    intercept, slope = float(beta[0]), float(beta[1])
    resid = y - design @ beta
    dof = n - 2
    sigma2 = float(resid @ resid) / dof
    se_slope = float(np.sqrt(max(0.0, sigma2 * xtx_inv[1, 1])))
    t_slope = slope / se_slope if se_slope > 1e-12 else 0.0
    return {
        "learned_process_noise": float(np.clip(slope, PROCESS_NOISE_MIN, PROCESS_NOISE_MAX)),
        "learned_process_noise_raw": slope,
        "intercept": intercept,
        "implied_measurement_variance": intercept / 2.0,
        "slope_se": se_slope,
        "slope_t": float(t_slope),
        "n_pairs": n,
    }


def fit_process_noise(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Fit every capacity axis present in ``frame``; return ``{axis: fit}``."""
    fits: dict[str, dict[str, Any]] = {}
    for axis, sub in frame.groupby(AXIS_COLUMN, sort=True):
        fits[str(axis)] = fit_axis(
            sub[FEATURE_COLUMN].to_numpy(dtype=float),
            sub[TARGET_COLUMN].to_numpy(dtype=float),
        )
    return fits


def build_artifact(
    frame: pd.DataFrame, *, source: str = "synthetic:random-walk-plus-noise"
) -> dict[str, Any]:
    """Build the frozen ``q10_confidence_calibration_v1`` shadow artifact from a frame."""
    fits = fit_process_noise(frame)
    defaults = default_process_noise()
    per_axis: dict[str, dict[str, Any]] = {}
    for axis, fit in fits.items():
        per_axis[axis] = {
            "learned_process_noise": round(fit["learned_process_noise"], 6),
            "default_process_noise": round(float(defaults.get(axis, 0.0025)), 6),
            "implied_measurement_variance": round(fit["implied_measurement_variance"], 6),
            "slope_t": round(fit["slope_t"], 3),
            "n_pairs": fit["n_pairs"],
        }
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "target": {
            "parameter": TARGET_PARAMETER,
            "applied": False,
            "binding": "unwired",
            "note": (
                "ADR-0036 process-noise is not consumed by any parameter-override loader; "
                "this artifact documents the learned-vs-default calibration for a future "
                "promotion and MUST NOT be applied to a live decision."
            ),
        },
        "reference_measured_variance": round(default_measured_variance(), 6),
        "per_axis": per_axis,
        "training": {
            "model": "per-axis OLS of squared_residual on elapsed_days (method-of-moments)",
            "estimator": "slope = process_noise q; intercept = 2·R (measurement floor)",
            "label": "squared residual between successive benchmark observations, [0,1]^2 units",
            "n_pairs": int(len(frame)),
            "n_axes": len(per_axis),
            "note": "Synthetic source: SHAPE/recovery only. See model_card.MODEL_CARD.",
        },
    }


def write_artifact(
    artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH
) -> Path:
    """Write the shadow artifact JSON (no override loader — the binding is unwired)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    observations = synthesize_observations(process_noise=0.01, seed=7)
    frame = build_frame(observations)
    artifact = build_artifact(frame)
    out = write_artifact(artifact)
    print(MODEL_CARD)
    print(f"\nwrote artifact -> {out}")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
