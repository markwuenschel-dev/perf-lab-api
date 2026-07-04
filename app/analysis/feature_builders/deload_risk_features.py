"""Q6: Deload risk dataset.

Prescription decisions joined to session feedback to capture athlete response.
Verified: prescription_decisions has athlete_id, created_at, decision_mode,
chosen_score, planned_session_id. session_feedback has planned_session_id,
status, satisfaction_score, pain_flag.

This module has two layers:

* ``build_dataset`` — the DB-backed source (async, Postgres) that pulls prescription
  decisions joined to session-feedback outcomes. This is the production-equivalent path.
* the pure, in-memory feature builders below (``assemble_deload_features`` and its
  helpers) — deterministic pandas transforms that turn per-(athlete, day) risk signals
  into the deload-risk feature frame the Q6 offline ML pipeline
  (``app.ml.q6_deload``) trains on. They are Postgres-free so the pipeline stays runnable
  and testable on synthetic fixtures. They are additive and do not change ``build_dataset``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Public group/order keys for the per-(athlete, day) feature frame.
GROUP_COLUMN = "athlete_id"
ORDER_COLUMN = "date"

# Trailing window (days) for the recent-trend slope features. Trailing-only (includes
# today and the past, never the future) so a slope is never contaminated by post-outcome
# data — the same "no look-ahead" rule the Q1/Q2 pipelines enforce.
SLOPE_WINDOW_DAYS = 7
MIN_SLOPE_PERIODS = 3

# The deload-risk feature columns produced by ``assemble_deload_features``. These are the
# signals the rule-based ``app.logic.deload_need.compute_deload_need`` consults (fatigue
# level + mean-fatigue slope, tissue load + slope, recent adherence, performance-residual
# slope) PLUS the Q1 decrement residual and the Q2 recovery-deficit residual (see below).
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


async def build_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    """Prescription decisions with feedback outcomes for deload risk modeling."""
    query = text("""
        SELECT
            pd.athlete_id,
            pd.created_at,
            pd.decision_mode,
            pd.chosen_score,
            pd.goal,
            sf.status,
            sf.satisfaction_score,
            sf.pain_flag,
            sf.soreness_flag,
            sf.followed_as_prescribed
        FROM prescription_decisions pd
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY pd.athlete_id, pd.created_at
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row) for row in result.mappings()]


# ---------------------------------------------------------------------------
# Pure feature builders (Postgres-free) for the Q6 offline ML pipeline.
# ---------------------------------------------------------------------------


def _trailing_slope(values: np.ndarray) -> float:
    """Ordinary-least-squares slope of ``values`` against a 0..n-1 time index.

    Positive = the signal is trending up over the trailing window. Uses only the values
    inside the window (which the caller restricts to today + the recent past), so it can
    never see the future.
    """
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
    """Per-athlete trailing OLS slope of ``col`` over a ``window``-day window.

    Grouped by athlete so a slope never spans two people, and trailing (the window ends at
    the current row) so it uses only today + the recent past. Rows without enough history
    yield NaN, which the caller imputes to 0.0 (neutral = no trend).
    """
    return df.groupby(group)[col].transform(
        lambda s: s.rolling(window, min_periods=min_periods).apply(_trailing_slope, raw=True)
    )


def _zscore_within_group(df: pd.DataFrame, col: str, *, group: str = GROUP_COLUMN) -> pd.Series:
    """z-score ``col`` within each athlete (removes cross-athlete scale differences).

    Mirrors ``app.ml.q1_decrement.build_training_frame._zscore_within_athlete``; a
    degenerate (single-row / zero-variance) athlete yields NaN.
    """
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

    Computes ``observed_rpe - E[rpe | planned load]`` using the *same* expectation-residual
    machinery the Q1 pipeline built (``app.ml.q1_decrement.build_training_frame``): a
    regularized linear expectation of session cost given the prescribed load, with the
    residual being the part of the athlete's reported effort the plan does NOT explain — a
    positive residual = performed worse than the plan should have cost = a performance
    decrement / carried fatigue. Higher = more deload risk. This is precisely the reuse Q1
    was built to enable.
    """
    from app.ml.q1_decrement import build_training_frame as q1_frame

    mini = pd.DataFrame(index=df.index)
    # Q1's primary planned-difficulty term; map our single planned-load signal onto it.
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
    """The Q2 recovery residual (negated to a deficit), reused as a deload-risk feature.

    Recomputes Q2's per-athlete-demeaned recovery-clearance residual
    (``clearance(t) - mean_athlete clearance``, the exact residual definition in
    ``app.ml.q2_recovery.build_training_frame``) from a TRAILING overnight-clearance signal,
    then negates it so higher = recovering WORSE than the athlete's own baseline = more
    deload risk. Per-athlete demeaning keeps this a within-athlete signal; the grouped split
    holds out whole athletes so the centering constant never crosses train/test.
    """
    clearance = df[clearance_col].astype(float)
    athlete_mean = clearance.groupby(df[group]).transform("mean")
    residual = clearance - athlete_mean
    return (-residual).to_numpy(dtype=float)


def assemble_deload_features(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Assemble the per-(athlete, day) deload-risk feature frame from raw daily signals.

    Expected raw columns per row: ``athlete_id``, ``date``, ``fatigue_mean``,
    ``fatigue_max``, ``tissue_max``, ``adherence``, ``session_rpe`` (performance cost),
    ``planned_load`` (prescribed load), ``recovery_clearance`` (trailing overnight fatigue
    clearance) and the raw daily outcome flag ``deload_event`` (1 on a day a deload was
    taken/needed). Returns ``athlete_id``, ``date``, ``deload_event`` and the
    ``DELOAD_FEATURE_COLUMNS``. All engineered features are pre-outcome for the day (levels,
    trailing slopes, or expectation residuals); the caller adds the forward-looking label.
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

    # Direct level features.
    out["fatigue_mean"] = df["fatigue_mean"].astype(float).to_numpy()
    out["fatigue_max"] = df["fatigue_max"].astype(float).to_numpy()
    out["tissue_max"] = df["tissue_max"].astype(float).to_numpy()
    out["adherence"] = df["adherence"].astype(float).to_numpy()

    # Residual features borrowed read-only from Q1 and Q2.
    out["q1_decrement"] = performance_decrement_residual(df)
    out["q2_recovery_deficit"] = recovery_deficit_residual(df)

    # Trailing-window slope features (recent trend, no look-ahead).
    df["_perf_residual"] = out["q1_decrement"].to_numpy()
    out["mean_fatigue_slope"] = rolling_slope(df, "fatigue_mean").to_numpy()
    out["tissue_slope"] = rolling_slope(df, "tissue_max").to_numpy()
    out["perf_residual_slope"] = rolling_slope(df, "_perf_residual").to_numpy()

    # Impute engineered NaNs (insufficient history / degenerate athlete) to neutral.
    for col in DELOAD_FEATURE_COLUMNS:
        out[col] = out[col].fillna(0.0)

    return out[[GROUP_COLUMN, ORDER_COLUMN, "deload_event", *DELOAD_FEATURE_COLUMNS]]
