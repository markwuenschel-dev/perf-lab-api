"""Pure (non-DB) tests for the Q6 deload-need offline ML pipeline.

Covers: (a) build_frame yields the expected columns with no leaked / label-derived
features and a leakage-clean forward label; (b) train() emits a reproducible shadow_only
artifact and predict_proba round-trips; (c) the promotion gate PROMOTES on a planted
deload signal and STAYS SHADOW on pure noise. Requires pandas/numpy/scikit-learn (dev
extra).
"""
from __future__ import annotations

import numpy as np

from app.ml.q6_deload.build_training_frame import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    HORIZON_DAYS,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
    synthetic_deload_rows,
)
from app.ml.q6_deload.evaluate import (
    MAX_CALIBRATION_ERROR,
    MIN_AUC_IMPROVEMENT,
    EvalReport,
    evaluate,
    rule_baseline_score,
)
from app.ml.q6_deload.feature_helpers import (
    DELOAD_FEATURE_COLUMNS,
    assemble_deload_features,
)
from app.ml.q6_deload.train import fit_deload_model, predict_proba, train


def test_build_frame_columns_and_no_leakage() -> None:
    frame = build_frame(synthetic_deload_rows(n_athletes=6, n_days=40, seed=1))

    for col in (GROUP_COLUMN, "date", *FEATURE_COLUMNS, LABEL_COLUMN):
        assert col in frame.columns
    assert len(frame) > 0

    # No forbidden / label-derived signal is exposed as a model feature.
    leaky = set(FORBIDDEN_FEATURES) - {"label"}
    assert leaky.isdisjoint(set(FEATURE_COLUMNS))
    assert "deload_event" not in FEATURE_COLUMNS
    assert "deload_event" not in frame.columns  # raw outcome flag not carried as a feature

    # Features are imputed (no NaN) so the logistic fit is well-posed.
    for feat in FEATURE_COLUMNS:
        assert not frame[feat].isna().any()

    # Label is binary and both classes appear on a planted fixture.
    assert set(np.unique(frame[LABEL_COLUMN])) <= {0.0, 1.0}
    assert frame[LABEL_COLUMN].nunique() == 2


def test_forward_label_drops_truncated_tail() -> None:
    rows = synthetic_deload_rows(n_athletes=3, n_days=30, seed=2)
    feats = assemble_deload_features(rows)
    frame = build_frame(rows)
    # The last HORIZON_DAYS rows of each athlete have a truncated forward window -> dropped.
    per_athlete_feat = feats.groupby(GROUP_COLUMN).size()
    per_athlete_frame = frame.groupby(GROUP_COLUMN).size()
    for aid, n in per_athlete_feat.items():
        assert per_athlete_frame[aid] == n - HORIZON_DAYS
    assert set(DELOAD_FEATURE_COLUMNS) <= set(feats.columns)


def test_train_emits_reproducible_shadow_only_artifact() -> None:
    frame = build_frame(synthetic_deload_rows(seed=3))
    artifact = train(frame)

    assert artifact["version"] == "q6_deload_priors_v1"
    assert artifact["namespace"] == "q6_deload"
    assert artifact["shadow_only"] is True
    assert artifact["horizon_days"] == HORIZON_DAYS
    # Records the (unwired) plug-in binding to the rule score.
    assert artifact["target"]["symbol"] == "DeloadNeed.score"
    assert artifact["features"] == list(FEATURE_COLUMNS)

    model = artifact["model"]
    assert model["type"] == "logistic_regression"
    assert set(model["coefficients"]) == set(FEATURE_COLUMNS)
    assert set(model["feature_means"]) == set(FEATURE_COLUMNS)

    # predict_proba round-trips and returns valid probabilities.
    proba = predict_proba(frame, artifact)
    assert proba.shape == (len(frame),)
    assert float(proba.min()) >= 0.0 and float(proba.max()) <= 1.0


def test_planted_signal_recovers_expected_coefficients() -> None:
    fit = fit_deload_model(build_frame(synthetic_deload_rows(effect=1.0, seed=4)))
    coefs = fit["coefficients"]
    # Higher fatigue/tissue/decrement/recovery-deficit -> more risk (positive); higher
    # adherence -> less risk (negative). Check the dominant, planted-in directions.
    assert coefs["fatigue_mean"] > 0
    assert coefs["q1_decrement"] > 0
    assert coefs["q2_recovery_deficit"] > 0
    assert coefs["adherence"] < 0


def test_rule_baseline_is_a_probability() -> None:
    frame = build_frame(synthetic_deload_rows(n_athletes=6, n_days=40, seed=5))
    score = rule_baseline_score(frame)
    assert score.shape == (len(frame),)
    assert float(score.min()) >= 0.0 and float(score.max()) <= 1.0


def test_planted_signal_promotes() -> None:
    frame = build_frame(synthetic_deload_rows(effect=1.0, seed=6))
    report = evaluate(frame)
    assert isinstance(report, EvalReport)
    assert report.auc_improvement > MIN_AUC_IMPROVEMENT
    assert report.brier_improvement > 0.0
    assert report.calibration_error <= MAX_CALIBRATION_ERROR
    assert report.sparse_brier_improvement >= -0.02
    assert report.verdict == "promote", report.reasons


def test_pure_noise_stays_shadow() -> None:
    frame = build_frame(synthetic_deload_rows(effect=0.0, seed=7))
    report = evaluate(frame)
    assert report.auc_improvement < MIN_AUC_IMPROVEMENT
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why


def test_report_serializes_with_expected_keys() -> None:
    d = evaluate(build_frame(synthetic_deload_rows(effect=0.6, seed=8))).as_dict()
    assert {
        "auc_rule", "auc_learned", "auc_improvement", "brier_rule", "brier_learned",
        "brier_improvement", "calibration_error", "sparse_brier_improvement",
        "verdict", "reasons",
    } <= set(d)


def test_grouped_split_holds_out_whole_athletes() -> None:
    frame = build_frame(synthetic_deload_rows(seed=9))
    train_df, test_df = grouped_time_split(frame)
    assert set(train_df[GROUP_COLUMN]).isdisjoint(set(test_df[GROUP_COLUMN]))
    assert len(train_df) + len(test_df) == len(frame)
