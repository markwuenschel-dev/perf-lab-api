"""Train the Q1 decrement pipeline: expectation model + decrement predictor (shadow-only).

Two stacked REGULARIZED LINEAR models (scikit-learn Ridge), no GBM / deep learning:

1. EXPECTATION  E[next_rpe | planned next-session difficulty]  (in build_training_frame).
   Its residual defines the label ``decrement = observed_next_rpe - expected_next_rpe``.
2. DECREMENT PREDICTOR  decrement ~ pre-next-session features (prev-session load/rpe, the
   session-stress fatigue proxy, the recovery gap, modality change). Alpha is chosen by
   athlete-grouped K-fold CV so no athlete straddles a fold.

There is NO engine plug-in: a decrement does not map to a single engine parameter, so this
emits a versioned, ``shadow_only`` research artifact only (see ``model_card`` for where it
would feed later). Run ``python -m app.ml.q1_decrement.train`` to build -> train -> print.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.ml.common.model_selection import select_alpha_grouped_cv
from app.ml.common.standardize import standardize_label
from app.ml.q1_decrement.build_training_frame import (
    GROUP_COLUMN,
    LABEL_COLUMN,
    OBSERVED_COLUMN,
    PREDICTOR_FEATURES,
    add_decrement_label,
    build_feature_frame,
    fit_expectation_model,
    grouped_time_split,
)
from app.ml.q1_decrement.model_card import MODEL_CARD

ARTIFACT_VERSION = "q1_decrement_v1"
NAMESPACE = "q1_decrement"


def labeled_partition(
    train_df: pd.DataFrame, test_df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fit the expectation on TRAIN only, then label both partitions with it.

    Refitting per-partition (rather than reusing ``build_frame``'s all-rows expectation)
    keeps the label DEFINITION from peeking at held-out athletes, so the gate measures the
    predictor honestly. Returns ``(train_labeled, test_labeled, expectation)``.
    """
    expectation = fit_expectation_model(train_df)
    train_l = add_decrement_label(train_df, expectation)
    test_l = add_decrement_label(test_df, expectation)
    return train_l, test_l, expectation


def fit_decrement_predictor(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit the pre-session decrement predictor; return coefficients + CV metadata.

    The label is standardized for coefficient comparability; alpha is chosen by
    athlete-grouped CV. Features are the pre-next-session ``PREDICTOR_FEATURES``.
    """
    x = frame.loc[:, list(PREDICTOR_FEATURES)].to_numpy(dtype=float)
    y_std, mean, std = standardize_label(frame[LABEL_COLUMN])
    groups = frame[GROUP_COLUMN].to_numpy()
    n_groups = int(np.unique(groups).size)

    alpha = select_alpha_grouped_cv(x, y_std, groups, n_groups)
    model = Ridge(alpha=alpha)
    model.fit(x, y_std)
    coefs = {f: float(c) for f, c in zip(PREDICTOR_FEATURES, model.coef_, strict=True)}
    return {
        "alpha": alpha,
        "coefficients": coefs,
        "label_mean": mean,
        "label_std": std,
        "n_rows": int(len(frame)),
        "n_athletes": n_groups,
    }


def train(
    rows: pd.DataFrame | list[dict[str, Any]],
    *,
    source: str = "synthetic:session-pairs",
) -> dict[str, Any]:
    """Build -> fit expectation -> fit decrement predictor; return the research artifact.

    Accepts raw session-pair rows (the ``session_decrement`` output shape). The artifact is
    ``shadow_only`` and has NO engine binding — it records the two learned models and the
    intended (future) feed point only.
    """
    features = build_feature_frame(rows)
    expectation = fit_expectation_model(features)
    labeled = add_decrement_label(features, expectation)
    predictor = fit_decrement_predictor(labeled)
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "engine_binding": None,  # a decrement maps to no single engine parameter
        "expectation_model": {
            "model": "ridge",
            "target": OBSERVED_COLUMN,
            "inputs": "planned next-session difficulty (prescribed load, pre-outcome)",
            "intercept": expectation["intercept"],
            "coefficients": expectation["coefficients"],
        },
        "decrement_predictor": {
            "model": "ridge",
            "alpha": predictor["alpha"],
            "coefficients": predictor["coefficients"],
            "label": "decrement = observed_next_rpe - expected_next_rpe (residual)",
            "n_rows": predictor["n_rows"],
            "n_athletes": predictor["n_athletes"],
        },
        "would_feed": (
            "shadow signal into the prescriber's expected-difficulty / readiness; "
            "not applied to any live decision. See model_card.MODEL_CARD."
        ),
        "note": "Synthetic source: SHAPE/weak-signal only.",
    }


def holdout_mae(frame: pd.DataFrame, *, holdout_frac: float = 0.25) -> tuple[float, float]:
    """MAE of the decrement predictor vs a neutral (predict-zero) baseline on holdout.

    Athletes are held out as whole groups; the expectation is refit on train only, then
    both partitions are labeled with it. The neutral baseline predicts 0 in
    standardized-label space (= "no fatigue decrement beyond the planned load"). Returns
    ``(mae_learned, mae_baseline)``.
    """
    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    train_l, test_l, _ = labeled_partition(train_df, test_df)

    x_tr = train_l.loc[:, list(PREDICTOR_FEATURES)].to_numpy(dtype=float)
    y_tr, mean, std = standardize_label(train_l[LABEL_COLUMN])
    x_te = test_l.loc[:, list(PREDICTOR_FEATURES)].to_numpy(dtype=float)
    y_te = (test_l[LABEL_COLUMN].to_numpy(dtype=float) - mean) / std

    pred = Ridge(alpha=1.0).fit(x_tr, y_tr).predict(x_te)
    mae_learned = float(np.mean(np.abs(y_te - pred)))
    mae_baseline = float(np.mean(np.abs(y_te - 0.0)))
    return mae_learned, mae_baseline


def main() -> None:
    from app.ml.q1_decrement.build_training_frame import build_frame
    from app.ml.q1_decrement.evaluate import evaluate

    # No production data source yet; demonstrate on a small planted-signal fixture.
    rng = np.random.default_rng(3)
    rows: list[dict[str, Any]] = []
    for a in range(24):
        for i in range(30):
            prev_rpe = float(rng.normal(6, 1.5))
            prev_dur = float(rng.normal(60, 12))
            prev_vol = float(rng.normal(4000, 800))
            gap = float(rng.uniform(24, 168))
            next_dur = float(rng.normal(60, 12))
            next_vol = float(rng.normal(4000, 800))
            load = prev_rpe * prev_dur
            decrement = 1.2 * (load - 360) / 180 - 1.0 * (gap - 96) / 42
            next_rpe = 0.03 * next_dur + 0.0004 * next_vol + decrement + rng.normal(0, 0.4)
            rows.append({
                "athlete_id": a, "prev_session_at": i,
                "prev_rpe": prev_rpe, "prev_duration_minutes": prev_dur,
                "prev_volume_load": prev_vol, "time_gap_hours": gap,
                "next_duration_minutes": next_dur, "next_volume_load": next_vol,
                "prev_modality": "strength", "next_modality": "strength",
                "next_rpe": next_rpe,
            })

    artifact = train(rows)
    report = evaluate(build_feature_frame(rows))
    print(MODEL_CARD)
    print(json.dumps(artifact, indent=2))
    print(json.dumps(report.as_dict(), indent=2))
    print(f"\nVERDICT: {report.verdict}")
    _ = build_frame  # canonical labeled-frame helper (kept importable)


if __name__ == "__main__":
    main()
