"""Q10 confidence-calibration pipeline tests (Postgres-free, synthetic fixtures).

Proves: (1) the frame builds clean consecutive-pair rows and holds athletes out as
whole groups; (2) the per-axis OLS RECOVERS a planted process-noise and its measurement
floor; (3) the gate PROMOTES on a real elapsed-days signal and STAYS SHADOW on pure
measurement noise. Requires pandas/numpy (dev extra).
"""
import numpy as np

from app.ml.q10_confidence.build_training_frame import (
    AXIS_COLUMN,
    FEATURE_COLUMN,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    MIN_ELAPSED_DAYS,
    TARGET_COLUMN,
    build_frame,
    grouped_split,
    synthesize_observations,
)
from app.ml.q10_confidence.evaluate import (
    MIN_CALIB_IMPROVEMENT,
    MIN_SLOPE_T,
    EvalReport,
    evaluate,
)
from app.ml.q10_confidence.train import (
    build_artifact,
    fit_axis,
    fit_process_noise,
)

PLANTED_Q = 0.01
PLANTED_MEAS_VAR = 0.02


def _signal_frame(seed: int = 7):
    obs = synthesize_observations(
        process_noise=PLANTED_Q, measurement_var=PLANTED_MEAS_VAR, seed=seed
    )
    return build_frame(obs)


def _noise_frame(seed: int = 11):
    obs = synthesize_observations(
        process_noise=0.0, measurement_var=PLANTED_MEAS_VAR, seed=seed
    )
    return build_frame(obs)


# --- frame construction & leakage handling ---------------------------------------


def test_frame_has_expected_columns_and_valid_pairs():
    frame = _signal_frame()
    assert {GROUP_COLUMN, AXIS_COLUMN, FEATURE_COLUMN, TARGET_COLUMN} <= set(frame.columns)
    assert len(frame) > 0
    # No first-of-series rows, no zero/negative elapsed, non-negative squared residuals.
    assert (frame[FEATURE_COLUMN] >= MIN_ELAPSED_DAYS).all()
    assert (frame[TARGET_COLUMN] >= 0.0).all()
    assert frame[FEATURE_COLUMN].notna().all()


def test_forbidden_features_document_leakage():
    # The target and the "predict obs2 with itself" trap must be named as leaks.
    assert "squared_residual" in FORBIDDEN_FEATURES
    assert any("predicted_from_state" in v or "predicting itself" in v
               for v in FORBIDDEN_FEATURES.values())


def test_grouped_split_holds_out_whole_athletes():
    frame = _signal_frame()
    train_df, test_df = grouped_split(frame, holdout_frac=0.25)
    train_ids = set(train_df[GROUP_COLUMN].unique())
    test_ids = set(test_df[GROUP_COLUMN].unique())
    assert train_ids and test_ids
    assert train_ids.isdisjoint(test_ids)  # no athlete straddles the split


# --- estimator recovery ----------------------------------------------------------


def test_fit_axis_recovers_planted_noise_and_measurement_floor():
    frame = _signal_frame()
    fits = fit_process_noise(frame)
    assert len(fits) == 4
    learned = np.array([f["learned_process_noise"] for f in fits.values()])
    imv = np.array([f["implied_measurement_variance"] for f in fits.values()])
    # The estimator is unbiased: the mean across axes recovers the planted noise tightly.
    assert abs(float(learned.mean()) - PLANTED_Q) < 0.002, learned
    # Method-of-moments on (chi-square) squared residuals is noisier per-axis; each axis
    # still lands in a sensible band and shows significant elapsed-days signal.
    for axis, fit in fits.items():
        assert abs(fit["learned_process_noise"] - PLANTED_Q) < 0.005, (axis, fit)
        assert fit["slope_t"] > MIN_SLOPE_T, (axis, fit)
    # Intercept ≈ 2·R recovers the measurement-noise floor on average.
    assert abs(float(imv.mean()) - PLANTED_MEAS_VAR) < 0.008, imv


def test_fit_axis_degenerate_returns_zero():
    fit = fit_axis(np.array([5.0, 5.0]), np.array([0.1, 0.2]))  # <3 pts / no elapsed var
    assert fit["learned_process_noise"] == 0.0
    assert fit["slope_t"] == 0.0


def test_noise_fixture_yields_near_zero_slope():
    fits = fit_process_noise(_noise_frame())
    # With no planted process-noise the slope is not a real signal.
    assert float(np.median([f["slope_t"] for f in fits.values()])) < MIN_SLOPE_T


# --- artifact --------------------------------------------------------------------


def test_artifact_is_shadow_only_with_unwired_binding():
    art = build_artifact(_signal_frame())
    assert art["shadow_only"] is True
    assert art["target"]["parameter"] == "EngineParameters.confidence_process_noise_per_day"
    assert art["target"]["applied"] is False
    assert art["target"]["binding"] == "unwired"
    assert art["per_axis"]  # learned-vs-default recorded per axis
    for axis_rec in art["per_axis"].values():
        assert "learned_process_noise" in axis_rec and "default_process_noise" in axis_rec


# --- gate ------------------------------------------------------------------------


def test_planted_signal_promotes():
    report = evaluate(_signal_frame())
    assert isinstance(report, EvalReport)
    assert report.median_slope_t >= MIN_SLOPE_T
    assert report.calib_improvement >= MIN_CALIB_IMPROVEMENT
    assert report.verdict == "promote", report.reasons


def test_pure_noise_stays_shadow():
    report = evaluate(_noise_frame())
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why (weak signal)


def test_report_serializes_with_expected_keys():
    d = evaluate(_signal_frame()).as_dict()
    assert {
        "n_test_pairs", "n_test_athletes", "n_axes", "learned_noise_mean",
        "default_noise_mean", "median_slope_t", "median_learned_noise",
        "calib_error_default", "calib_error_learned", "calib_improvement",
        "per_axis", "verdict", "reasons",
    } <= set(d)
