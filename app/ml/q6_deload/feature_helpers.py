"""Pure (Postgres-free) feature builders for the Q6 offline deload-risk pipeline.

Deterministic pandas transforms that turn per-(athlete, day) risk signals into the
deload-risk feature frame the Q6 model trains on. These live under ``app/ml`` (the
offline layer) rather than in ``app.analysis.feature_builders`` so the SQL feature-builder
stays pandas-free/strictly-typed; the DB-backed source there is
``deload_risk_features.build_dataset``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Public group/order keys for the per-(athlete, day) feature frame.
GROUP_COLUMN = "athlete_id"
ORDER_COLUMN = "date"

# Trailing window (days) for the recent-trend slope features. Trailing-only (includes
# today and the past, never the future) so a slope is never contaminated by post-outcome
# data — the same "no look-ahead" rule the Q1/Q2 pipelines enforce.
SLOPE_WINDOW_DAYS = 7
MIN_SLOPE_PERIODS = 3

# The deload-risk feature columns produced by ``assemble_deload_features``: the signals the
# rule-based ``app.logic.deload_need.compute_deload_need`` consults (fatigue level +
# mean-fatigue slope, tissue load + slope, recent adherence, performance-residual slope)
# PLUS the Q1 decrement residual and the Q2 recovery-deficit residual.
DELOAD_FEATURE_COLUMNS: tuple[str, ...] = (
    "fatigue_mean",
    "fatigue_max",
    "mean_fatigue_slope",
    "tissue_max",
    "tissue_slope",
    "adherence",
    "perf_residual_slope",
    "q1_decrement",
    "q2_recovery_deficit",
)


def _trailing_slope(values: np.ndarray) -> float:
    """OLS slope of ``values`` against a 0..n-1 index (positive = trending up)."""
    n = len(values)
    if n < 2:
        return np.nan
    x = np.arange(n, dtype=float)
    xm = x.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom <= 0.0:
        return 0.0
    ym = values.mean()
    return float(((x - xm) * (values - ym)).sum() / denom)


def rolling_slope(
    df: pd.DataFrame,
    col: str,
    *,
    group: str = GROUP_COLUMN,
    window: int = SLOPE_WINDOW_DAYS,
    min_periods: int = MIN_SLOPE_PERIODS,
) -> pd.Series:
    """Per-athlete trailing OLS slope of ``col`` (grouped, trailing → no look-ahead)."""
    return df.groupby(group)[col].transform(
        lambda s: s.rolling(window, min_periods=min_periods).apply(_trailing_slope, raw=True)
    )


def _zscore_within_group(df: pd.DataFrame, col: str, *, group: str = GROUP_COLUMN) -> pd.Series:
    """z-score ``col`` within each athlete (degenerate athlete → NaN)."""
    grp = df.groupby(group)[col]
    mean = grp.transform("mean")
    std = grp.transform("std")
    std = std.where(std > 1e-9)
    z = (df[col] - mean) / std
    return z.replace([np.inf, -np.inf], np.nan)


def performance_decrement_residual(
    df: pd.DataFrame,
    *,
    group: str = GROUP_COLUMN,
    perf_col: str = "session_rpe",
    planned_col: str = "planned_load",
) -> np.ndarray:
    """The Q1 decrement residual, reused read-only as a deload-risk feature.

    ``observed_rpe - E[rpe | planned load]`` via the Q1 expectation machinery — a positive
    residual = performed worse than the plan should have cost = carried fatigue.
    """
    from app.ml.q1_decrement import build_training_frame as q1_frame

    mini = pd.DataFrame(index=df.index)
    mini["z_next_duration"] = _zscore_within_group(df, planned_col, group=group).fillna(0.0)
    mini["z_next_volume"] = 0.0
    mini["modality_change"] = 0.0
    mini[q1_frame.OBSERVED_COLUMN] = df[perf_col].astype(float).to_numpy()

    expectation = q1_frame.fit_expectation_model(mini)
    labeled = q1_frame.add_decrement_label(mini, expectation)
    return labeled[q1_frame.LABEL_COLUMN].to_numpy(dtype=float)


def recovery_deficit_residual(
    df: pd.DataFrame,
    *,
    group: str = GROUP_COLUMN,
    clearance_col: str = "recovery_clearance",
) -> np.ndarray:
    """The Q2 recovery residual (per-athlete demeaned, negated to a deficit).

    Higher = recovering worse than the athlete's own baseline = more deload risk.
    """
    clearance = df[clearance_col].astype(float)
    athlete_mean = clearance.groupby(df[group]).transform("mean")
    residual = clearance - athlete_mean
    return (-residual).to_numpy(dtype=float)


def assemble_deload_features(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Assemble the per-(athlete, day) deload-risk feature frame from raw daily signals.

    Raw columns per row: ``athlete_id``, ``date``, ``fatigue_mean``, ``fatigue_max``,
    ``tissue_max``, ``adherence``, ``session_rpe``, ``planned_load``, ``recovery_clearance``,
    and the raw daily outcome flag ``deload_event``. All engineered features are pre-outcome
    for the day (levels, trailing slopes, or expectation residuals); the caller adds the
    forward-looking label.
    """
    df = pd.DataFrame(rows).copy()
    df[ORDER_COLUMN] = pd.to_datetime(df[ORDER_COLUMN])
    df = df.sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)

    out = pd.DataFrame(
        {
            GROUP_COLUMN: df[GROUP_COLUMN].to_numpy(),
            ORDER_COLUMN: df[ORDER_COLUMN].to_numpy(),
            "deload_event": df["deload_event"].astype(float).to_numpy(),
        }
    )

    out["fatigue_mean"] = df["fatigue_mean"].astype(float).to_numpy()
    out["fatigue_max"] = df["fatigue_max"].astype(float).to_numpy()
    out["tissue_max"] = df["tissue_max"].astype(float).to_numpy()
    out["adherence"] = df["adherence"].astype(float).to_numpy()

    out["q1_decrement"] = performance_decrement_residual(df)
    out["q2_recovery_deficit"] = recovery_deficit_residual(df)

    df["_perf_residual"] = out["q1_decrement"].to_numpy()
    out["mean_fatigue_slope"] = rolling_slope(df, "fatigue_mean").to_numpy()
    out["tissue_slope"] = rolling_slope(df, "tissue_max").to_numpy()
    out["perf_residual_slope"] = rolling_slope(df, "_perf_residual").to_numpy()

    for col in DELOAD_FEATURE_COLUMNS:
        out[col] = out[col].fillna(0.0)

    return out[[GROUP_COLUMN, ORDER_COLUMN, "deload_event", *DELOAD_FEATURE_COLUMNS]]
