"""Grouped-holdout time split shared by the offline ML pipelines.

Holds out whole GROUPS (athletes / decisions) so no group straddles train/test — this
keeps per-group baselines, residualizations and standardizations from leaking across the
split — while preserving each group's internal ordering. Parameterized by the group key
and the trailing order columns; each pipeline passes its own constants.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def grouped_time_split(
    frame: pd.DataFrame,
    *,
    group_column: str,
    order_columns: Sequence[str],
    holdout_frac: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hold out the highest-id ``holdout_frac`` of whole groups; preserve within-group order.

    Groups are partitioned by ``group_column`` (sorted, last ``round(n*frac)`` held out, at
    least one) so none appears in both partitions. Rows in each partition are sorted by
    ``[group_column, *order_columns]``. Byte-identical to the per-pipeline copies it
    replaces (q1/q2/q3/q6/q9/dose_calibration).
    """
    ids = np.sort(frame[group_column].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[group_column].isin(test_ids)
    order = [group_column, *order_columns]
    train_df = frame[~is_test].sort_values(order).reset_index(drop=True)
    test_df = frame[is_test].sort_values(order).reset_index(drop=True)
    return train_df, test_df
