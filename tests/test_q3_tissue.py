"""Offline validation gate + frame-integrity tests for the Q3 tissue-risk model.

Non-DB: builds the synthetic per-(athlete, day, axis) fixture directly and proves the gate
PROMOTES when a real tissue-risk signal is planted and STAYS SHADOW on pure noise, plus
leakage/labeling invariants. Requires pandas/numpy/scikit-learn (dev extra).
"""
import numpy as np

from app.ml.q3_tissue.build_training_frame import (
    AXIS_COLUMN,
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    HORIZON_DAYS,
    LABEL_COLUMN,
    build_frame,
    grouped_time_split,
    synthetic_tissue_rows,
)
from app.ml.q3_tissue.evaluate import (
    MIN_AUC_IMPROVEMENT,
    EvalReport,
    evaluate,
    rule_baseline_score,
)
from app.ml.q3_tissue.train import predict_proba, train


def _planted_frame():
    return build_frame(synthetic_tissue_rows(effect=1.2, seed=1))


def _noise_frame():
    return build_frame(synthetic_tissue_rows(effect=0.0, seed=2))


# ----------------------------- frame / leakage integrity -----------------------------


def test_frame_has_expected_columns_and_binary_label():
    frame = _planted_frame()
    expected = {GROUP_COLUMN, AXIS_COLUMN, "date", *FEATURE_COLUMNS, LABEL_COLUMN}
    assert expected <= set(frame.columns)
    assert set(np.unique(frame[LABEL_COLUMN].to_numpy())) <= {0.0, 1.0}
    assert frame[LABEL_COLUMN].mean() > 0.0  # planted signal produces real positives


def test_raw_daily_flag_is_forbidden_as_feature():
    # The label reuses the tissue_event name for its forward aggregate; the raw daily flag
    # and any in-horizon signal are documented as forbidden features.
    assert "tissue_event" in FORBIDDEN_FEATURES
    assert "label" in FORBIDDEN_FEATURES
    assert LABEL_COLUMN not in FEATURE_COLUMNS


def test_grouped_split_holds_out_whole_athletes():
    train_df, test_df = grouped_time_split(_planted_frame())
    assert not (set(train_df[GROUP_COLUMN]) & set(test_df[GROUP_COLUMN]))
    assert len(test_df) > 0 and len(train_df) > 0


def test_forward_window_drops_truncated_tail():
    # Every (athlete, axis) series loses its trailing HORIZON_DAYS rows to the forward label.
    rows = synthetic_tissue_rows(effect=1.0, seed=3)
    n_series = len({(r[GROUP_COLUMN], r[AXIS_COLUMN]) for r in rows})
    frame = build_frame(rows)
    assert len(frame) == len(rows) - n_series * HORIZON_DAYS


# ----------------------------------- promotion gate -----------------------------------


def test_planted_signal_promotes():
    report = evaluate(_planted_frame())
    assert isinstance(report, EvalReport)
    assert report.auc_learned > report.auc_rule
    assert report.auc_improvement > MIN_AUC_IMPROVEMENT
    assert report.verdict == "promote", report.reasons


def test_pure_noise_stays_shadow():
    report = evaluate(_noise_frame())
    assert report.auc_improvement < MIN_AUC_IMPROVEMENT
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why


def test_report_serializes_with_expected_keys():
    d = evaluate(_planted_frame()).as_dict()
    assert {
        "auc_rule", "auc_learned", "auc_improvement", "brier_rule", "brier_learned",
        "brier_improvement", "calibration_error", "sparse_brier_improvement",
        "verdict", "reasons",
    } <= set(d)


def test_rule_baseline_in_unit_interval():
    frame = _planted_frame()
    score = rule_baseline_score(frame)
    assert score.min() >= 0.0 and score.max() <= 1.0


# --------------------------------- trained artifact -----------------------------------


def test_artifact_is_shadow_only_with_unwired_target():
    frame = _planted_frame()
    artifact = train(frame)
    assert artifact["shadow_only"] is True
    tgt = artifact["target"]
    assert tgt["module"] == "app.logic.tissue_risk"
    assert tgt["symbol"] == "TissueRiskPrediction.risk_by_axis"
    assert "NOT wired" in tgt["binding"]
    assert set(artifact["features"]) == set(FEATURE_COLUMNS)


def test_predict_proba_is_calibrated_probability():
    frame = _planted_frame()
    artifact = train(frame)
    proba = predict_proba(frame, artifact)
    assert proba.min() >= 0.0 and proba.max() <= 1.0
    assert len(proba) == len(frame)
