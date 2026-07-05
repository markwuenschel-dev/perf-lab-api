"""Standardization helpers shared by the offline ML pipelines.

Label centering/scaling (so ridge coefficients are comparable), per-feature column
standardization (so logistic coefficients are comparable), and within-group / trailing-
baseline z-scores. Byte-identical to the per-pipeline copies these replace.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def standardize_label(y: pd.Series | np.ndarray) -> tuple[np.ndarray, float, float]:
    """Center + scale to unit variance; return ``(standardized, mean, std)`` (std=0 -> 1.0).

    Accepts a Series or ndarray. Replaces q1/q2 ``_standardize_label`` (Series input) and
    dose_calibration ``_standardize`` (Series or ndarray).
    """
    arr = np.asarray(y, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=0)) or 1.0
    return (arr - mean) / std, mean, std


def standardize_columns(
    frame: pd.DataFrame, feature_columns: Sequence[str]
) -> tuple[np.ndarray, dict[str, float], dict[str, float]]:
    """Return standardized feature matrix + per-feature mean/std (std=0 -> 1.0).

    Replaces the q3/q6 ``_standardize_columns`` helper.
    """
    x = frame.loc[:, list(feature_columns)].to_numpy(dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std > 1e-9, std, 1.0)
    z = (x - mean) / std
    means = {f: float(m) for f, m in zip(feature_columns, mean, strict=True)}
    stds = {f: float(s) for f, s in zip(feature_columns, std, strict=True)}
    return z, means, stds


def zscore_within_group(
    df: pd.DataFrame, col: str, *, group_column: str
) -> pd.Series:
    """z-score ``col`` within each group (degenerate/zero-variance group -> NaN).

    Replaces q1 ``_zscore_within_athlete`` and q6 ``_zscore_within_group``.
    """
    grp = df.groupby(group_column)[col]
    mean = grp.transform("mean")
    std = grp.transform("std")
    std = std.where(std > 1e-9)
    z = (df[col] - mean) / std
    return z.replace([np.inf, -np.inf], np.nan)


def zscore_vs_trailing_baseline(
    df: pd.DataFrame,
    col: str,
    *,
    group_column: str,
    window_days: int,
    min_baseline_days: int,
) -> pd.Series:
    """z-score ``col`` against each group's trailing baseline, excluding the current row.

    Baseline mean/std come from a rolling ``window_days`` window shifted by one so the
    current observation never enters its own baseline. Degenerate baseline / insufficient
    history -> NaN. Replaces q2 ``_zscore_vs_trailing_baseline``.
    """
    grp = df.groupby(group_column)[col]
    mean = grp.transform(
        lambda s: s.shift(1).rolling(window_days, min_periods=min_baseline_days).mean()
    )
    std = grp.transform(
        lambda s: s.shift(1).rolling(window_days, min_periods=min_baseline_days).std()
    )
    std = std.where(std > 1e-9)
    z = (df[col] - mean) / std
    return z.replace([np.inf, -np.inf], np.nan)
