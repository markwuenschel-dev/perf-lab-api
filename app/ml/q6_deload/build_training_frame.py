"""Build the Q6 deload-need training frame (offline, shadow-only).

Turns a per-(athlete, day) stream of deload-risk signals into a supervised frame for
learning a CALIBRATED probability

    P(a deload is needed in the next k days | today's risk state)

that can augment/replace the hand-set ``DeloadNeed.score`` produced by the rule-based
``app.logic.deload_need.compute_deload_need``. The features are exactly the signals that
rule consults — fatigue level + mean-fatigue slope, tissue load + slope, recent adherence,
performance-residual slope — PLUS two residual features borrowed read-only from the
already-shipped Q1 and Q2 pipelines (see ``deload_risk_features``):

* ``q1_decrement`` — the Q1 next-session decrement residual (performed worse than the
  planned load should have cost = carried fatigue).
* ``q2_recovery_deficit`` — the negated Q2 recovery-clearance residual (recovering worse
  than the athlete's own baseline).

The label is FORWARD-looking: whether a deload actually occurred within the next ``k``
days. Real first-party deload outcomes are thin (now capturable via SessionFeedback /
telemetry — see the model card), so the pipeline is built to run and be tested on a
synthetic fixture (``synthetic_deload_rows``) with a planted deload-risk signal. The
production-equivalent source is the DB-backed
``app.analysis.feature_builders.deload_risk_features.build_dataset``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.analysis.feature_builders.deload_risk_features import (
    DELOAD_FEATURE_COLUMNS,
    GROUP_COLUMN,
    ORDER_COLUMN,
    assemble_deload_features,
)

# Prediction horizon: a deload "needed in the next k days".
HORIZON_DAYS = 7

FEATURE_COLUMNS: tuple[str, ...] = DELOAD_FEATURE_COLUMNS
LABEL_COLUMN = "deload_needed"

# Features that are FORBIDDEN because they leak the forward label or are measured during /
# after the horizon being predicted. The label aggregates the raw daily ``deload_event``
# flag over t+1..t+k, so that flag (and anything dated inside the horizon) is post-outcome.
FORBIDDEN_FEATURES: dict[str, str] = {
    "deload_event": "raw daily deload-occurred flag; the label is its forward (t+1..t+k) aggregate -> direct leak",
    "deload_needed": "the supervised target itself",
    "label": "the supervised target itself",
    "future_fatigue": "any fatigue/tissue/adherence value dated within the t+1..t+k horizon is measured DURING/AFTER the window being predicted",
    "future_recovery_clearance": "an overnight clearance dated inside the horizon is post-outcome",
    "deload_taken_next": "whether a deload was taken in the horizon IS the outcome, not an input",
}


def _forward_any_event(frame: pd.DataFrame, horizon: int) -> tuple[pd.Series, pd.Series]:
    """Per-athlete: was there any ``deload_event`` in the next ``horizon`` days?

    Returns ``(label, full_window)``. ``label`` is 1.0 if any of days t+1..t+horizon carried
    a deload event, else 0.0. ``full_window`` marks rows whose entire ``horizon``-day forward
    window is observed; the trailing ``horizon`` rows of each athlete's series (a partial /
    truncated window that could undercount events) are dropped by the caller. Assumes
    daily-consecutive rows within an athlete (the synthetic fixture guarantees this; a gapped
    DB source would add a date-difference guard, mirroring Q2's ``gap_days`` check).
    """
    ev = frame["deload_event"]
    shifted = [frame.groupby(GROUP_COLUMN)["deload_event"].shift(-i) for i in range(1, horizon + 1)]
    fwd = pd.concat(shifted, axis=1)
    full_window = fwd.notna().all(axis=1)
    label = (fwd.fillna(0.0).to_numpy() > 0).any(axis=1).astype(float)
    return pd.Series(label, index=ev.index), full_window


def build_frame(
    rows: pd.DataFrame | list[dict[str, Any]], *, horizon: int = HORIZON_DAYS
) -> pd.DataFrame:
    """Build the supervised deload-need frame: risk features + the forward P(deload) label.

    Assembles the pre-outcome feature frame (levels, trailing slopes, Q1/Q2 residuals) via
    ``assemble_deload_features``, then attaches the forward-looking binary label ``was a
    deload needed within the next ``horizon`` days``. Rows whose forward window is fully
    truncated (the last ``horizon`` days of each athlete) are dropped. Returns
    ``athlete_id``, ``date``, the ``FEATURE_COLUMNS`` and ``deload_needed``.
    """
    feats = assemble_deload_features(rows)
    feats = feats.sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)

    label, full_window = _forward_any_event(feats, horizon)
    feats[LABEL_COLUMN] = label
    out = feats[full_window.to_numpy()].reset_index(drop=True)

    return out[[GROUP_COLUMN, ORDER_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN]]


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped), preserving per-athlete time order.

    Athletes are partitioned by id so none appears in both train and test — this keeps the
    within-athlete residuals/slopes and any per-athlete standardization from leaking across
    the split. Rows stay in ``(athlete_id, date)`` order within each partition. Mirrors
    ``app.ml.q2_recovery.build_training_frame.grouped_time_split``.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    train_df = frame[~is_test].sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    test_df = frame[is_test].sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    return train_df, test_df


def synthetic_deload_rows(
    *,
    n_athletes: int = 40,
    n_days: int = 90,
    effect: float = 1.0,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Generate a synthetic per-(athlete, day) raw-signal fixture with a planted signal.

    A smooth per-athlete latent risk (a normalized random walk, so recent TREND is
    informative) drives, scaled by ``effect``: fatigue/tissue levels up, adherence down,
    session RPE above its planned-load expectation (feeding the Q1 residual), overnight
    recovery clearance down (feeding the Q2 deficit), AND the per-day probability of a
    deload event. Because the latent risk is autocorrelated, today's signals predict the
    near-future events — a genuine, learnable deload signal.

    With ``effect=0`` the signals are pure noise and events are an i.i.d. low base rate,
    independent of the features — the pipeline's honest "no signal" case, on which the gate
    must STAY SHADOW.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-01")
    rows: list[dict[str, Any]] = []
    for a in range(n_athletes):
        walk = np.cumsum(rng.normal(0.0, 0.3, n_days))
        risk = (walk - walk.mean()) / (walk.std() or 1.0)
        for d in range(n_days):
            rk = float(effect * risk[d])
            planned = float(rng.normal(300.0, 60.0))
            session_rpe = 5.0 + 0.004 * planned + 1.3 * rk + float(rng.normal(0.0, 0.6))
            fatigue_mean = float(np.clip(42.0 + 11.0 * rk + rng.normal(0.0, 5.0), 0.0, 100.0))
            fatigue_max = float(np.clip(fatigue_mean + 12.0 + 8.0 * rk + rng.normal(0.0, 5.0), 0.0, 100.0))
            tissue_max = float(np.clip(38.0 + 12.0 * rk + rng.normal(0.0, 6.0), 0.0, 100.0))
            adherence = float(np.clip(0.85 - 0.11 * rk + rng.normal(0.0, 0.05), 0.0, 1.0))
            recovery_clearance = 2.0 - 1.2 * rk + float(rng.normal(0.0, 0.8))
            logit = -3.0 + 1.9 * rk
            event = 1.0 if rng.random() < 1.0 / (1.0 + np.exp(-logit)) else 0.0
            rows.append(
                {
                    "athlete_id": a,
                    "date": (start + pd.Timedelta(days=d)).date().isoformat(),
                    "fatigue_mean": fatigue_mean,
                    "fatigue_max": fatigue_max,
                    "tissue_max": tissue_max,
                    "adherence": adherence,
                    "session_rpe": session_rpe,
                    "planned_load": planned,
                    "recovery_clearance": recovery_clearance,
                    "deload_event": event,
                }
            )
    return rows
