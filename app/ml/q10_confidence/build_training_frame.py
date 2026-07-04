"""Build the Q10 confidence-calibration training frame (Rail 1, shadow-only).

Turns a stream of benchmark observations into a variance-calibration frame: one row
per CONSECUTIVE observation pair on a capacity axis, carrying ``elapsed_days`` and the
``squared_residual`` between the second observation and the model's prediction carried
from the first. Fitting ``squared_residual ~ intercept + slope * elapsed_days`` per
axis recovers the per-day process-noise (slope) and the measurement-noise floor
(intercept ≈ 2·R); see ``train`` / ``model_card``.

WHY successive-difference residuals. In the engine (ADR-0036) a capacity axis is a
latent value observed with noise; between measurements only time grows its variance
(``_grow_confidence_variance``: ``var += q · dt``), and a benchmark pulls the axis to
the measurement and shrinks the variance (``_apply_capacity_residual`` Kalman gain).
A full-weight benchmark therefore anchors the state to the measurement, so the best
OFFLINE reconstruction of the model's prediction for observation 2 is observation 1's
normalized value. The pair residual ``y2 - y1`` is then the innovation of a
random-walk-plus-noise process with

    E[(y2 - y1)^2 | dt] = q · dt + 2·R

— exactly the predicted-variance-vs-observed-squared-residual relation the engine
encodes, which is what makes the slope a method-of-moments estimate of ``q``.

The production-equivalent, DB-backed feed is
``app.analysis.feature_builders.confidence_calibration_features`` (benchmark
observation sequences with ``LAG(observed_at)`` per athlete×benchmark). That module is
SQL-only and pandas-free by design; this frame keeps the pipeline runnable and testable
without Postgres via ``synthesize_observations``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

# Capacity-confidence axes (EngineParameters.confidence_process_noise_per_day keys /
# CapacityConfidence.KEYS). The frame fits only the axes actually present in the data.
AXES: tuple[str, ...] = (
    "aerobic",
    "glycolytic",
    "max_strength",
    "hypertrophy",
    "power",
    "skill",
    "mobility",
    "work_capacity",
)

# Benchmark ``normalized_value`` arrives on a 0–100 scale (bo.normalized_value); the
# engine works in [0, 1] state units (score01 = normalized_value / 100). Variance is
# therefore reported in [0, 1]^2 units, directly comparable to
# confidence_process_noise_per_day / confidence_measured_variance.
NORMALIZED_SCALE = 100.0

GROUP_COLUMN = "athlete_id"
AXIS_COLUMN = "axis"
FEATURE_COLUMN = "elapsed_days"     # the single predictor of predicted variance
TARGET_COLUMN = "squared_residual"  # the observed variance realization

# Pairs closer than this in time carry no usable process-noise signal (and a 0 elapsed
# would divide-by-zero the calibration); drop them.
MIN_ELAPSED_DAYS = 0.5

# Columns the frame is allowed to expose to the fit.
FRAME_COLUMNS: tuple[str, ...] = (
    GROUP_COLUMN,
    AXIS_COLUMN,
    "observed_at",
    FEATURE_COLUMN,
    "predicted_from_state",
    "observed_norm",
    TARGET_COLUMN,
)

# Features that are FORBIDDEN because they leak the calibration target (the squared
# residual) or are measured after the interval whose variance is being predicted.
FORBIDDEN_FEATURES: dict[str, str] = {
    "obs2_normalized_value_as_prediction": (
        "using observation 2's own value as predicted_from_state drives the residual to "
        "0 — the target predicting itself; the prediction must come only from observation 1"
    ),
    "future_observation_value": (
        "any observation at t+2 or later is post-outcome for the (obs1→obs2) interval "
        "whose variance is being calibrated"
    ),
    "obs2_raw_value": (
        "the raw value at observation 2 encodes the normalized value the squared residual "
        "is built from — direct leak of the target"
    ),
    "obs2_observation_weight": (
        "the measurement weight/variance of observation 2 is realized WITH the outcome; "
        "using it to set the predicted variance leaks the measurement-noise term"
    ),
    "obs2_observed_at_in_prediction": (
        "observation 2's timestamp defines the elapsed interval but must never feed the "
        "predicted STATE mean — only elapsed_days (obs2 - obs1) is a legitimate predictor"
    ),
    "squared_residual": "the calibration target itself",
}


def _to_normalized01(values: pd.Series) -> pd.Series:
    """Scale 0–100 ``normalized_value`` into the engine's [0, 1] state units."""
    return values.astype(float) / NORMALIZED_SCALE


def build_frame(observations: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Build the consecutive-pair variance-calibration frame.

    ``observations`` is any iterable of mappings shaped like the DB feed rows
    (``app.analysis.feature_builders.confidence_calibration_features``), each with:

    * ``athlete_id`` — athlete grouping key,
    * ``axis`` — the capacity target axis (benchmark_code → mapping.target_key upstream;
      supplied directly here so the frame is DB-agnostic),
    * ``normalized_value`` — the 0–100 benchmark score,
    * ``observed_at`` — timestamp of the observation.

    Returns one row per CONSECUTIVE (athlete, axis) observation pair with
    ``elapsed_days`` (obs2 − obs1), ``predicted_from_state`` (obs1 normalized to [0, 1]),
    ``observed_norm`` (obs2 in [0, 1]) and ``squared_residual`` = (obs2 − obs1)^2. The
    first observation of each (athlete, axis) series and any pair closer than
    ``MIN_ELAPSED_DAYS`` produce no row.
    """
    df = pd.DataFrame(list(observations))
    if df.empty:
        return pd.DataFrame(columns=list(FRAME_COLUMNS))

    df = df[[GROUP_COLUMN, AXIS_COLUMN, "normalized_value", "observed_at"]].copy()
    df["observed_at"] = pd.to_datetime(df["observed_at"])
    df["observed_norm"] = _to_normalized01(df["normalized_value"])
    df = df.sort_values([GROUP_COLUMN, AXIS_COLUMN, "observed_at"]).reset_index(drop=True)

    grp = df.groupby([GROUP_COLUMN, AXIS_COLUMN], sort=False)
    # predicted_from_state is ONLY the previous observation (the anchored state), never
    # obs2 itself — see FORBIDDEN_FEATURES.
    prev_norm = grp["observed_norm"].shift(1)
    prev_time = grp["observed_at"].shift(1)
    elapsed_days = (df["observed_at"] - prev_time).dt.total_seconds() / 86400.0

    out = df[[GROUP_COLUMN, AXIS_COLUMN, "observed_at", "observed_norm"]].copy()
    out["predicted_from_state"] = prev_norm
    out[FEATURE_COLUMN] = elapsed_days
    out[TARGET_COLUMN] = (out["observed_norm"] - out["predicted_from_state"]) ** 2

    out = out[out[FEATURE_COLUMN].notna() & (out[FEATURE_COLUMN] >= MIN_ELAPSED_DAYS)]
    out = out.reset_index(drop=True)
    return out[list(FRAME_COLUMNS)]


def grouped_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hold out whole athletes so a latent trajectory never straddles train/test.

    Splitting by athlete prevents the per-athlete random-walk (whose successive
    residuals are correlated across the same series) from leaking between the fit and the
    calibration check. Rows stay in ``(athlete, axis, observed_at)`` order.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    if len(ids) < 2:
        return frame.copy(), frame.iloc[0:0].copy()
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    order = [GROUP_COLUMN, AXIS_COLUMN, "observed_at"]
    train_df = frame[~is_test].sort_values(order).reset_index(drop=True)
    test_df = frame[is_test].sort_values(order).reset_index(drop=True)
    return train_df, test_df


def synthesize_observations(
    *,
    process_noise: float,
    measurement_var: float = 0.02,
    n_athletes: int = 100,
    axes: Sequence[str] = AXES[:4],
    obs_per_series: int = 12,
    elapsed_choices: Sequence[float] = (2.0, 5.0, 9.0, 14.0, 20.0, 28.0),
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Plant a known per-day process-noise and emit benchmark-observation rows.

    Each (athlete, axis) latent capacity ``x`` follows a random walk in [0, 1] units with
    per-step variance ``process_noise · elapsed_days`` and is observed with additive
    Gaussian measurement noise of variance ``measurement_var``:

        x_{k+1} = x_k + N(0, process_noise · dt_k)
        y_k     = x_k + N(0, measurement_var)          (reported as 0–100)

    so ``E[(y_{k+1} - y_k)^2 | dt] = process_noise · dt + 2·measurement_var`` and the
    frame's ``squared_residual`` slope recovers ``process_noise``. Pass
    ``process_noise=0`` for a pure-measurement-noise (no-signal) fixture. The latent walk
    is intentionally left unclamped so the variance relation stays exactly linear (a
    reflecting boundary would bias the recovered slope); values may fall slightly outside
    0–100.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-01")
    rows: list[dict[str, Any]] = []
    meas_sd = float(np.sqrt(max(0.0, measurement_var)))
    for athlete in range(n_athletes):
        for axis in axes:
            x = 0.5
            t = start
            for _ in range(obs_per_series):
                y = x + rng.normal(0.0, meas_sd)
                rows.append(
                    {
                        GROUP_COLUMN: athlete,
                        AXIS_COLUMN: axis,
                        "normalized_value": float(y * NORMALIZED_SCALE),
                        "observed_at": t,
                    }
                )
                dt = float(rng.choice(np.asarray(elapsed_choices, dtype=float)))
                x = x + rng.normal(0.0, float(np.sqrt(max(0.0, process_noise) * dt)))
                t = t + pd.Timedelta(days=dt)
    return rows
