"""Build the Q1 next-session-decrement training frame (offline, shadow-only).

Turns the session-pair rows emitted by
``app.analysis.feature_builders.session_decrement`` into a supervised frame whose label
is a RESIDUAL, not a raw next-session value:

    decrement = observed_next_rpe - expected_next_rpe_given_plan

WHY A RESIDUAL (and not raw next_rpe)
  Raw next-session RPE conflates two very different things: how HARD the planned session
  was (a bigger prescribed load naturally earns a higher RPE) and how much WORSE than
  planned the athlete performed (accumulated fatigue / a performance decrement). We only
  want the second. So we first fit an expectation model
  ``E[next_rpe | planned next-session difficulty]`` (a simple regularized linear model
  over the *prescribed* next-session load) and take the residual. A positive residual —
  the athlete reported a higher RPE than the planned load should have cost — means the
  session was harder than it should have been, i.e. a performance decrement / carried
  fatigue. The expectation model is defined here because it defines the label; ``train``
  consumes it and adds the pre-session decrement PREDICTOR on top.

The pipeline is runnable/testable on a synthetic session-pair frame (see the tests); the
production-equivalent source is the DB-backed ``session_decrement.build_dataset`` over
``workout_logs``, kept Postgres-free here.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

GROUP_COLUMN = "athlete_id"
ORDER_COLUMN = "prev_session_at"  # per-athlete time order for the grouped time split

# PRE-next-session features the decrement PREDICTOR is allowed to use. Every one is known
# strictly before the next session is performed. ``z_prev_load`` (z-scored prev_rpe *
# prev_duration) is the session-stress / accumulated-fatigue proxy; a short ``z_time_gap``
# plus a high recent load is the classic "not recovered yet" signature.
PREDICTOR_FEATURES: tuple[str, ...] = (
    "z_prev_rpe",
    "z_prev_duration",
    "z_prev_volume",
    "z_prev_load",
    "z_time_gap",
    "modality_change",
)

# Inputs to the EXPECTATION model E[next_rpe | planned difficulty]. These describe the
# *prescribed* next session (its planned duration/volume + whether the modality switched),
# which is known in advance from the plan — so they are legitimate pre-outcome inputs to
# the expectation, NOT leakage. They are deliberately NOT predictor features: the residual
# already has planned difficulty removed, so re-feeding it would only re-introduce it.
PLANNED_FEATURES: tuple[str, ...] = ("z_next_duration", "z_next_volume", "modality_change")

OBSERVED_COLUMN = "next_rpe"        # the outcome; forbidden as a predictor feature
EXPECTED_COLUMN = "expected_next_rpe"
LABEL_COLUMN = "decrement"

# Features that are FORBIDDEN as decrement-PREDICTOR inputs because they leak the label or
# are measured post-outcome. The only next-session information allowed anywhere is the
# PLANNED difficulty (next_duration_minutes / next_volume_load / next_modality), and that
# only feeds the expectation model.
FORBIDDEN_FEATURES: dict[str, str] = {
    "next_rpe": "observed next-session outcome; label = next_rpe - expected_next_rpe, so it leaks the label directly",
    "expected_next_rpe": "the expectation term the label is built from; correlated with the label by construction",
    "decrement": "the supervised target itself",
    "next_performance": "any MEASURED outcome of the next session (other than its prescribed load) is post-outcome",
    "next_rpe_derived": "any transform of the observed next-session RPE is post-outcome",
}

# Source (session_decrement) columns each z-scored feature is derived from.
_RAW_SOURCE: dict[str, str] = {
    "z_prev_rpe": "prev_rpe",
    "z_prev_duration": "prev_duration_minutes",
    "z_prev_volume": "prev_volume_load",
    "z_time_gap": "time_gap_hours",
    "z_next_duration": "next_duration_minutes",
    "z_next_volume": "next_volume_load",
}


def _zscore_within_athlete(df: pd.DataFrame, raw_col: str) -> pd.Series:
    """z-score ``raw_col`` within each athlete (removes cross-athlete scale differences).

    Standardizing within an athlete keeps the fit comparable across people with very
    different absolute RPE/volume habits. Because the grouped time split holds out WHOLE
    athletes, a within-athlete z-score never mixes information across the train/test
    boundary. Degenerate (single-row / zero-variance) athletes yield NaN -> imputed to
    0.0 (neutral = at that athlete's own average).
    """
    grp = df.groupby(GROUP_COLUMN)[raw_col]
    mean = grp.transform("mean")
    std = grp.transform("std")
    std = std.where(std > 1e-9)
    z = (df[raw_col] - mean) / std
    return z.replace([np.inf, -np.inf], np.nan)


def build_feature_frame(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Build the pre-label feature frame from session-pair rows (no decrement yet).

    Emits the z-scored predictor + planned features, the ``modality_change`` flag, and the
    observed ``next_rpe`` (kept so the expectation model can be fit and the residual taken,
    but never used as a predictor feature). One row per input session pair.
    """
    df = pd.DataFrame(rows).copy()
    if ORDER_COLUMN not in df.columns:
        # Synthesize a stable per-athlete order when no timestamp is supplied.
        df[ORDER_COLUMN] = df.groupby(GROUP_COLUMN).cumcount()
    df = df.sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)

    # Session-stress / fatigue proxy: how much work the previous session cost.
    df["prev_load"] = df["prev_rpe"].astype(float) * df["prev_duration_minutes"].astype(float)
    df["modality_change"] = (
        df["prev_modality"].astype(str) != df["next_modality"].astype(str)
    ).astype(float)

    for feat, raw_col in _RAW_SOURCE.items():
        df[feat] = _zscore_within_athlete(df, raw_col)
    df["z_prev_load"] = _zscore_within_athlete(df, "prev_load")

    keep = [GROUP_COLUMN, ORDER_COLUMN, *PREDICTOR_FEATURES, *PLANNED_FEATURES, OBSERVED_COLUMN]
    keep = list(dict.fromkeys(keep))  # dedupe modality_change (in both feature groups)
    out = df[keep].copy()
    for feat in (*PREDICTOR_FEATURES, *PLANNED_FEATURES):
        out[feat] = out[feat].fillna(0.0)
    out[OBSERVED_COLUMN] = out[OBSERVED_COLUMN].astype(float)
    return out.reset_index(drop=True)


def fit_expectation_model(frame: pd.DataFrame, *, alpha: float = 1.0) -> dict[str, Any]:
    """Fit E[next_rpe | planned difficulty] — a regularized linear model (ridge).

    Kept intentionally simple and regularized so the expectation captures only the coarse
    "bigger planned load -> higher expected RPE" relationship; whatever it cannot explain
    from the plan becomes the decrement residual.
    """
    x = frame.loc[:, list(PLANNED_FEATURES)].to_numpy(dtype=float)
    y = frame[OBSERVED_COLUMN].to_numpy(dtype=float)
    model = Ridge(alpha=alpha)
    model.fit(x, y)
    coefs = {f: float(c) for f, c in zip(PLANNED_FEATURES, model.coef_, strict=True)}
    return {"alpha": float(alpha), "intercept": float(model.intercept_), "coefficients": coefs}


def predict_expected(frame: pd.DataFrame, expectation: dict[str, Any]) -> np.ndarray:
    """Apply a fitted expectation model to ``frame`` -> expected next_rpe per row."""
    coefs = expectation["coefficients"]
    pred = np.full(len(frame), float(expectation["intercept"]))
    for feat, c in coefs.items():
        pred = pred + float(c) * frame[feat].to_numpy(dtype=float)
    return pred


def add_decrement_label(frame: pd.DataFrame, expectation: dict[str, Any]) -> pd.DataFrame:
    """Attach ``expected_next_rpe`` and the residual ``decrement`` label to ``frame``."""
    out = frame.copy()
    out[EXPECTED_COLUMN] = predict_expected(frame, expectation)
    out[LABEL_COLUMN] = out[OBSERVED_COLUMN].to_numpy(dtype=float) - out[EXPECTED_COLUMN]
    return out


def build_frame(rows: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Build the full supervised frame: session-pair features + the residual decrement.

    Convenience wrapper: features -> fit a POPULATION expectation on all rows -> attach the
    residual label. This is the canonical labeled frame (the expectation here is the label
    DEFINITION). For a leakage-clean gate, ``evaluate``/``holdout`` re-fit the expectation
    on the train partition only rather than reusing this all-rows definition.
    """
    features = build_feature_frame(rows)
    expectation = fit_expectation_model(features)
    return add_decrement_label(features, expectation)


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped) while preserving per-athlete order.

    Athletes are partitioned by id so none appears in both train and test — this keeps the
    within-athlete z-scores (and any per-partition expectation refit) from leaking across
    the split. Rows stay in ``(athlete_id, order)`` order within each partition. Mirrors
    ``app.ml.q2_recovery.build_training_frame.grouped_time_split``.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    train_df = frame[~is_test].sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    test_df = frame[is_test].sort_values([GROUP_COLUMN, ORDER_COLUMN]).reset_index(drop=True)
    return train_df, test_df
