"""Pure (non-DB) tests for the Q1 next-session-decrement pipeline.

Covers, on synthetic session-pair fixtures built directly here:
  (a) the residual label is correct — expected_next_rpe tracks the PLANNED difficulty and
      ``decrement = observed_next_rpe - expected_next_rpe`` recovers the planted fatigue
      signal, with planned difficulty removed;
  (b) no leaked / label-derived column is exposed as a PREDICTOR feature;
  (c) the promotion gate PROMOTES when a real decrement signal is planted in pre-session
      features and STAYS SHADOW on pure noise;
  (d) train() emits the shadow-only, engine-unbound research artifact.
Requires pandas/numpy/scikit-learn (dev extra).
"""
from __future__ import annotations

import numpy as np

from app.ml.q1_decrement.build_training_frame import (
    EXPECTED_COLUMN,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    LABEL_COLUMN,
    OBSERVED_COLUMN,
    PLANNED_FEATURES,
    PREDICTOR_FEATURES,
    build_feature_frame,
    build_frame,
)
from app.ml.q1_decrement.evaluate import (
    MIN_IMPROVEMENT,
    MIN_SIGN_ACCURACY,
    EvalReport,
    evaluate,
)
from app.ml.q1_decrement.train import train

# True planned-difficulty (expectation) coefficients used to synthesize next_rpe.
_A_DUR, _A_VOL = 0.03, 0.0004


def _rows(
    decrement_effect: float,
    noise: float,
    *,
    n_athletes: int = 26,
    n: int = 30,
    seed: int = 0,
) -> tuple[list[dict[str, object]], np.ndarray]:
    """Session-pair rows where next_rpe = plan-difficulty + planted decrement + noise.

    The planted decrement depends only on PRE-session fatigue signals (previous-session
    load and the recovery gap), so a correct residual label must recover it and the gate
    must be able to predict it. Returns ``(rows, planted)`` in matching order.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    planted: list[float] = []
    for a in range(n_athletes):
        for i in range(n):
            prev_rpe = float(rng.normal(6, 1.5))
            prev_dur = float(rng.normal(60, 12))
            prev_vol = float(rng.normal(4000, 800))
            gap = float(rng.uniform(24, 168))
            next_dur = float(rng.normal(60, 12))
            next_vol = float(rng.normal(4000, 800))
            load = prev_rpe * prev_dur
            # Higher recent load and a SHORTER recovery gap => bigger decrement.
            p = decrement_effect * ((load - 360.0) / 180.0) - decrement_effect * 0.8 * (
                (gap - 96.0) / 42.0
            )
            plan = _A_DUR * next_dur + _A_VOL * next_vol
            next_rpe = plan + p + float(rng.normal(0, noise))
            rows.append(
                {
                    "athlete_id": a,
                    "prev_session_at": i,
                    "prev_rpe": prev_rpe,
                    "prev_duration_minutes": prev_dur,
                    "prev_volume_load": prev_vol,
                    "time_gap_hours": gap,
                    "next_duration_minutes": next_dur,
                    "next_volume_load": next_vol,
                    "prev_modality": "strength",
                    "next_modality": "strength",
                    "next_rpe": next_rpe,
                }
            )
            planted.append(p)
    return rows, np.asarray(planted)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a, b)[0, 1])


def test_feature_frame_columns_and_no_nan() -> None:
    rows, _ = _rows(decrement_effect=1.0, noise=0.4)
    frame = build_feature_frame(rows)
    for col in (GROUP_COLUMN, *PREDICTOR_FEATURES, *PLANNED_FEATURES, OBSERVED_COLUMN):
        assert col in frame.columns
    assert len(frame) == len(rows)
    for feat in (*PREDICTOR_FEATURES, *PLANNED_FEATURES):
        assert not frame[feat].isna().any(), feat


def test_no_leakage_in_predictor_features() -> None:
    # No forbidden / label-derived column is a predictor feature.
    assert set(FORBIDDEN_FEATURES).isdisjoint(set(PREDICTOR_FEATURES))
    # The observed outcome, the expectation term and the label are never predictor inputs.
    for banned in (OBSERVED_COLUMN, EXPECTED_COLUMN, LABEL_COLUMN):
        assert banned in FORBIDDEN_FEATURES or banned == LABEL_COLUMN
        assert banned not in PREDICTOR_FEATURES
    # The only next-session info the predictor may not see is anything beyond planned load;
    # planned difficulty lives ONLY in the expectation inputs, not the predictor set.
    assert "next_rpe" not in PREDICTOR_FEATURES
    assert set(PLANNED_FEATURES) - {"modality_change"} == {"z_next_duration", "z_next_volume"}


def test_residual_label_is_correct() -> None:
    """expected_next_rpe tracks the PLAN; decrement recovers the planted fatigue signal."""
    rows, planted = _rows(decrement_effect=1.2, noise=0.25, seed=1)
    labeled = build_frame(rows)

    true_plan = np.asarray(
        [_A_DUR * r["next_duration_minutes"] + _A_VOL * r["next_volume_load"] for r in rows]
    )
    expected = labeled[EXPECTED_COLUMN].to_numpy()
    decrement = labeled[LABEL_COLUMN].to_numpy()

    # The expectation model reconstructs the planned-difficulty component.
    assert _corr(expected, true_plan) > 0.95
    # The residual recovers the planted decrement...
    assert _corr(decrement, planted) > 0.9
    # ...with planned difficulty removed (residual ~orthogonal to the plan).
    assert abs(_corr(decrement, true_plan)) < 0.2
    # A residual is mean-zero overall (ridge intercept).
    assert abs(float(decrement.mean())) < 1e-6


def test_raw_next_rpe_would_conflate_plan_and_decrement() -> None:
    """Sanity: raw next_rpe is contaminated by plan difficulty; the residual is not."""
    rows, planted = _rows(decrement_effect=1.2, noise=0.25, seed=2)
    labeled = build_frame(rows)
    raw = labeled[OBSERVED_COLUMN].to_numpy()
    true_plan = np.asarray(
        [_A_DUR * r["next_duration_minutes"] + _A_VOL * r["next_volume_load"] for r in rows]
    )
    decrement = labeled[LABEL_COLUMN].to_numpy()
    # Raw next_rpe correlates with plan difficulty; the decrement label does not.
    assert _corr(raw, true_plan) > 0.3
    assert abs(_corr(decrement, true_plan)) < abs(_corr(raw, true_plan))


def test_gate_promotes_on_planted_decrement_signal() -> None:
    rows, _ = _rows(decrement_effect=1.4, noise=0.4, seed=3)
    report = evaluate(build_feature_frame(rows))
    assert isinstance(report, EvalReport)
    assert report.improvement > MIN_IMPROVEMENT
    assert report.sign_accuracy > MIN_SIGN_ACCURACY
    assert report.sparse_improvement >= 0.0
    assert report.saturation_fraction <= 0.05
    assert report.verdict == "promote", report.reasons


def test_gate_stays_shadow_on_pure_noise() -> None:
    rows, _ = _rows(decrement_effect=0.0, noise=1.0, seed=4)
    report = evaluate(build_feature_frame(rows))
    assert report.improvement < MIN_IMPROVEMENT
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why


def test_report_serializes_with_expected_keys() -> None:
    rows, _ = _rows(decrement_effect=0.8, noise=0.5, seed=5)
    d = evaluate(build_feature_frame(rows)).as_dict()
    assert {
        "mae_baseline",
        "mae_learned",
        "improvement",
        "sign_accuracy",
        "calibration_error",
        "sparse_improvement",
        "saturation_fraction",
        "verdict",
        "reasons",
    } <= set(d)


def test_train_emits_shadow_only_unbound_artifact() -> None:
    rows, _ = _rows(decrement_effect=1.2, noise=0.4, seed=6)
    artifact = train(rows)
    assert artifact["version"] == "q1_decrement_v1"
    assert artifact["namespace"] == "q1_decrement"
    assert artifact["shadow_only"] is True
    assert artifact["engine_binding"] is None  # decrement maps to no single engine param
    # Both learned stages are recorded.
    assert set(artifact["expectation_model"]["coefficients"]) == set(PLANNED_FEATURES)
    assert set(artifact["decrement_predictor"]["coefficients"]) == set(PREDICTOR_FEATURES)
    assert "prescriber" in artifact["would_feed"]


def test_planted_predictor_recovers_expected_signs() -> None:
    """Higher prev-load => more decrement (+); longer recovery gap => less decrement (-)."""
    rows, _ = _rows(decrement_effect=1.4, noise=0.3, seed=7)
    artifact = train(rows)
    coefs = artifact["decrement_predictor"]["coefficients"]
    assert coefs["z_prev_load"] > 0
    assert coefs["z_time_gap"] < 0
