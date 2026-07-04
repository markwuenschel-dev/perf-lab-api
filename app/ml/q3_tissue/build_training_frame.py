"""Build the Q3 tissue-risk training frame (offline, shadow-only).

Turns a per-(athlete, day, tissue-axis) stream of tissue-load / exposure signals into a
supervised frame for learning a CALIBRATED probability

    P(a tissue/pain event at this axis within the next k days | today's exposure state)

that can eventually replace the hand-set per-axis scores in
``app.logic.tissue_risk.TissueRiskPrediction.risk_by_axis`` (today ``calibrated=False``).
The features are exactly the quantities the rule-based ``compute_tissue_risk`` consults —
current tissue load, ACWR-style acute:chronic exposure (3d/7d/28d), recent concentration,
a prior-pain flag — PLUS same-day fatigue, which the rule ignores and which gives the
learned model room to beat the rule.

The label is FORWARD-looking: whether a tissue event (``tissue_skip`` / ``tissue_modified``
/ ``pain_event``) actually occurred at that axis in the next ``k`` days. Real first-party
tissue outcomes are thin, so the pipeline is built to run and be tested on a synthetic
fixture (``synthetic_tissue_rows``) with a planted, autocorrelated tissue-risk signal. The
production-equivalent, DB-backed source is
``app.analysis.feature_builders.tissue_risk_features.build_dataset`` (``outcome_events``
of tissue type joined to same-day ``wellness_samples``).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Grouping / ordering keys for the per-(athlete, day, axis) frame. Whole athletes are held
# out for CV (GROUP_COLUMN); a per-axis time series is keyed by (athlete, axis).
GROUP_COLUMN = "athlete_id"
AXIS_COLUMN = "tissue_axis"
ORDER_COLUMN = "date"

# Prediction horizon: a tissue event "in the next k days".
HORIZON_DAYS = 7

# ACWR-style exposure windows (days). Mirrors the 3d/7d/28d lagged exposures the rule
# consults; the 28d "chronic" is expressed as a weekly-equivalent (d28 / 4) as in the rule.
ACUTE_CONCENTRATION_DAYS = 3
ACUTE_DAYS = 7
CHRONIC_DAYS = 28
# Trailing window (days) over which a recent tissue event sets the prior-pain flag.
PRIOR_PAIN_DAYS = 7

# The pre-outcome feature columns produced by ``assemble_tissue_features``.
FEATURE_COLUMNS: tuple[str, ...] = (
    "tissue_load",      # current accumulated tissue stress at the axis (0..100)
    "acute_exposure",   # 7-day cumulative dose (acute exposure magnitude)
    "acwr",             # acute:chronic ratio = d7 / (d28 / 4)  (>1.3 = spike, per the rule)
    "concentration",    # d3 / d7  (how concentrated the acute dose is in the last 3 days)
    "prior_pain",       # 1.0 if a tissue event hit this axis in the trailing PRIOR_PAIN_DAYS
    "fatigue",          # same-day fatigue level (rule ignores this; the model's edge)
)
LABEL_COLUMN = "tissue_event"

# Features FORBIDDEN because they leak the forward label or are measured during/after the
# horizon being predicted. The label aggregates the raw daily tissue-event flag over
# t+1..t+k, so that flag today/ahead is post-outcome; prior_pain is allowed ONLY because it
# reads strictly-past (<= t-1) events.
FORBIDDEN_FEATURES: dict[str, str] = {
    "tissue_event": "raw daily tissue-event flag; the label is its forward (t+1..t+k) aggregate -> direct leak",
    "tissue_event_next": "any tissue-event flag dated inside the t+1..t+k horizon is the outcome, not an input",
    "future_tissue_load": "a tissue-load value dated inside the horizon is measured DURING/AFTER the predicted window",
    "future_exposure": "a dose/exposure dated inside the horizon is post-outcome",
    "same_day_pain": "the day-t pain flag itself is part of the outcome stream; only strictly-past (<= t-1) pain feeds prior_pain",
    "label": "the supervised target itself",
}


def _grouped_rolling_sum(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    """Per-(athlete, axis) trailing rolling SUM of ``col`` (trailing -> no look-ahead)."""
    return df.groupby([GROUP_COLUMN, AXIS_COLUMN])[col].transform(
        lambda s: s.rolling(window, min_periods=1).sum()
    )


def assemble_tissue_features(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Assemble the per-(athlete, day, axis) tissue-risk feature frame from raw signals.

    Raw columns per row: ``athlete_id``, ``date``, ``tissue_axis``, ``tissue_dose`` (daily
    impulse), ``tissue_load`` (accumulated state 0..100), ``fatigue`` (0..100), and the raw
    daily outcome flag ``tissue_event``. All engineered features are pre-outcome for day t:
    the ACWR-style exposures are TRAILING cumulative sums, and ``prior_pain`` reads only
    strictly-past events (shift(1) before the trailing window). The caller adds the
    forward-looking label.
    """
    df = pd.DataFrame(rows).copy()
    df[ORDER_COLUMN] = pd.to_datetime(df[ORDER_COLUMN])
    df = df.sort_values([GROUP_COLUMN, AXIS_COLUMN, ORDER_COLUMN]).reset_index(drop=True)

    d3 = _grouped_rolling_sum(df, "tissue_dose", ACUTE_CONCENTRATION_DAYS)
    d7 = _grouped_rolling_sum(df, "tissue_dose", ACUTE_DAYS)
    d28 = _grouped_rolling_sum(df, "tissue_dose", CHRONIC_DAYS)

    # Acute:chronic ratio, mirroring compute_tissue_risk: chronic expressed weekly (d28/4);
    # neutral 1.0 when there is no chronic base yet. Clipped to keep an early-history spike
    # from dominating the feature.
    chronic_weekly = (d28 / 4.0).to_numpy(dtype=float)
    d7_arr = d7.to_numpy(dtype=float)
    d3_arr = d3.to_numpy(dtype=float)
    acwr = np.where(chronic_weekly > 0.0, d7_arr / np.maximum(chronic_weekly, 1e-6), 1.0)
    acwr = np.clip(acwr, 0.0, 3.0)
    concentration = np.where(d7_arr > 0.0, d3_arr / np.maximum(d7_arr, 1e-6), 0.0)

    # prior_pain: any tissue event in the trailing PRIOR_PAIN_DAYS, strictly BEFORE today
    # (shift(1) excludes the same-day event, which belongs to the outcome stream).
    prior_pain = df.groupby([GROUP_COLUMN, AXIS_COLUMN])[LABEL_COLUMN].transform(
        lambda s: s.shift(1).rolling(PRIOR_PAIN_DAYS, min_periods=1).max()
    )
    prior_pain = (prior_pain.fillna(0.0).to_numpy(dtype=float) > 0.0).astype(float)

    out = pd.DataFrame(
        {
            GROUP_COLUMN: df[GROUP_COLUMN].to_numpy(),
            AXIS_COLUMN: df[AXIS_COLUMN].to_numpy(),
            ORDER_COLUMN: df[ORDER_COLUMN].to_numpy(),
            LABEL_COLUMN: df[LABEL_COLUMN].astype(float).to_numpy(),
            "tissue_load": df["tissue_load"].astype(float).to_numpy(),
            "acute_exposure": d7_arr,
            "acwr": acwr,
            "concentration": concentration,
            "prior_pain": prior_pain,
            "fatigue": df["fatigue"].astype(float).to_numpy(),
        }
    )
    for col in FEATURE_COLUMNS:
        out[col] = out[col].fillna(0.0)
    return out


def _forward_any_event(frame: pd.DataFrame, horizon: int) -> tuple[pd.Series, pd.Series]:
    """Per-(athlete, axis): was there any tissue event in the next ``horizon`` days?

    Returns ``(label, full_window)``. ``label`` is 1.0 if any of days t+1..t+horizon carried
    a tissue event at that axis, else 0.0. ``full_window`` marks rows whose entire
    ``horizon``-day forward window is observed; the trailing ``horizon`` rows of each
    (athlete, axis) series (a truncated window that could undercount events) are dropped by
    the caller. Mirrors ``app.ml.q6_deload.build_training_frame._forward_any_event``.
    """
    grp = frame.groupby([GROUP_COLUMN, AXIS_COLUMN])[LABEL_COLUMN]
    shifted = [grp.shift(-i) for i in range(1, horizon + 1)]
    fwd = pd.concat(shifted, axis=1)
    full_window = fwd.notna().all(axis=1)
    label = (fwd.fillna(0.0).to_numpy() > 0).any(axis=1).astype(float)
    return pd.Series(label, index=frame.index), full_window


def build_frame(
    rows: pd.DataFrame | list[dict[str, Any]], *, horizon: int = HORIZON_DAYS
) -> pd.DataFrame:
    """Build the supervised tissue-risk frame: exposure features + forward P(event) label.

    Assembles the pre-outcome feature frame, then attaches the forward-looking binary label
    ``was there a tissue event at this axis within the next ``horizon`` days``. Rows whose
    forward window is truncated (the last ``horizon`` days of each (athlete, axis) series)
    are dropped. Returns ``athlete_id``, ``tissue_axis``, ``date``, the ``FEATURE_COLUMNS``
    and ``tissue_event`` (the forward label; the raw same-day flag is overwritten here).
    """
    feats = assemble_tissue_features(rows)
    feats = feats.sort_values([GROUP_COLUMN, AXIS_COLUMN, ORDER_COLUMN]).reset_index(drop=True)

    label, full_window = _forward_any_event(feats, horizon)
    feats[LABEL_COLUMN] = label.to_numpy()
    out = feats[full_window.to_numpy()].reset_index(drop=True)

    return out[[GROUP_COLUMN, AXIS_COLUMN, ORDER_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN]]


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole ATHLETES (grouped), preserving per-(athlete, axis) time order.

    Athletes are partitioned by id so none appears in both train and test — this keeps the
    trailing exposures, the prior-pain flag and any per-athlete standardization from leaking
    across the split, and keeps every axis of an athlete on the same side. Mirrors
    ``app.ml.q6_deload.build_training_frame.grouped_time_split``.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    order = [GROUP_COLUMN, AXIS_COLUMN, ORDER_COLUMN]
    train_df = frame[~is_test].sort_values(order).reset_index(drop=True)
    test_df = frame[is_test].sort_values(order).reset_index(drop=True)
    return train_df, test_df


# Default axes for the synthetic fixture — a representative subset of TissueState.KEYS
# (shoulder, elbow, wrist, lumbar, hip, knee, ankle, finger). The model pools across axes
# (a population prior), so a subset keeps the fixture fast without changing its shape.
_DEFAULT_AXES: tuple[str, ...] = ("shoulder", "elbow", "lumbar", "knee")


def synthetic_tissue_rows(
    *,
    n_athletes: int = 24,
    n_days: int = 80,
    axes: tuple[str, ...] = _DEFAULT_AXES,
    effect: float = 1.0,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Generate a synthetic per-(athlete, day, axis) raw-signal fixture with a planted signal.

    A smooth per-(athlete, axis) latent tissue-risk (a normalized random walk, so recent
    TREND is informative) drives, scaled by ``effect``: the daily tissue dose up (raising the
    3d/7d/28d exposures and the ACWR spike), the accumulated tissue load up, same-day fatigue
    up, AND the per-day probability of a tissue event. Because the latent risk is
    autocorrelated, today's exposure state predicts near-future events — a genuine, learnable
    tissue-risk signal. Fatigue is a comparatively CLEAN proxy for the latent risk while
    tissue_load/ACWR are noisier; since the rule baseline ignores fatigue, the learned model
    has room to beat it.

    With ``effect=0`` the signals are pure noise and events are an i.i.d. low base rate,
    independent of the features — the honest "no signal" case, on which the gate must STAY
    SHADOW.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-01")
    rows: list[dict[str, Any]] = []
    for a in range(n_athletes):
        for axis in axes:
            walk = np.cumsum(rng.normal(0.0, 0.3, n_days))
            risk = (walk - walk.mean()) / (walk.std() or 1.0)
            for d in range(n_days):
                rk = float(effect * risk[d])
                tissue_dose = float(max(0.0, 5.0 + 3.0 * rk + rng.normal(0.0, 1.5)))
                tissue_load = float(np.clip(35.0 + 18.0 * rk + rng.normal(0.0, 10.0), 0.0, 100.0))
                fatigue = float(np.clip(40.0 + 15.0 * rk + rng.normal(0.0, 6.0), 0.0, 100.0))
                logit = -2.4 + 2.2 * rk
                event = 1.0 if rng.random() < 1.0 / (1.0 + np.exp(-logit)) else 0.0
                rows.append(
                    {
                        GROUP_COLUMN: a,
                        AXIS_COLUMN: axis,
                        ORDER_COLUMN: (start + pd.Timedelta(days=d)).date().isoformat(),
                        "tissue_dose": tissue_dose,
                        "tissue_load": tissue_load,
                        "fatigue": fatigue,
                        LABEL_COLUMN: event,
                    }
                )
    return rows
