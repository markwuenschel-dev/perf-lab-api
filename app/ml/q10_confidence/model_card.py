"""Model card for the Q10 confidence-calibration artifact (``q10_confidence_calibration_v1``).

Kept as source so provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q10 CONFIDENCE-CALIBRATION -- MODEL CARD (q10_confidence_calibration_v1)
======================================================================

WHAT THIS IS
  An offline calibration of the engine's per-axis capacity PROCESS-NOISE
    EngineParameters.confidence_process_noise_per_day   (ADR-0036)
  so the model's PREDICTED capacity-axis variance matches the OBSERVED squared
  residual between successive benchmark observations. Emitted as a versioned JSON
  artifact recording learned-vs-default per-axis noise. This is a variance /
  reliability calibration, NOT a point-prediction model and NOT personalization.

THE ENGINE MODEL BEING CALIBRATED (ADR-0036)
  A capacity axis is a latent value observed with noise. Between measurements only
  time grows its variance:
    app.logic.state_update_v0._grow_confidence_variance:  var += q * dt   (q per axis)
  A benchmark pulls the axis toward the measurement and shrinks the variance via a
  scalar Kalman gain:
    app.logic.state_update_v0._apply_capacity_residual / kalman_gain, with
    measurement variance R = confidence_measured_variance / observation_weight.
  So a full-weight benchmark anchors the state to the measurement, and the offline
  reconstruction of the model's prediction for observation 2 is observation 1.

CALIBRATION TARGET (method of moments)
  For consecutive observations y1, y2 on an axis separated by ``dt`` days, the pair
  residual is the innovation of a random-walk-plus-noise process:
    E[(y2 - y1)^2 | dt] = q * dt + 2*R
  Fitting the per-axis OLS line  squared_residual = intercept + slope*elapsed_days
  gives:
    slope     = q   -> learned process_noise (method-of-moments estimate)
    intercept = 2*R -> measurement-noise floor (cross-check vs confidence_measured_variance)

DATA SOURCE
  Production-equivalent DB feed (SQL-only, pandas-free, NOT modified here):
    app.analysis.feature_builders.confidence_calibration_features
    -- benchmark_observations joined to benchmark_definitions, with
      LAG(observed_at) OVER (PARTITION BY user_id, benchmark_definition_id
      ORDER BY observed_at) supplying prev_observed_at for the elapsed interval.
  benchmark_code -> capacity axis is the definition's mapping.target_key upstream.

  *** SYNTHETIC CAVEAT ***
  This pipeline is runnable/testable without Postgres via
  build_training_frame.synthesize_observations, which plants a KNOWN per-day
  process-noise so the fit can be shown to recover it. Synthetic data proves the
  ESTIMATOR and the GATE; it does not set production magnitudes -- the central reason
  the artifact is shadow_only.

LABEL / FRAME
  One row per consecutive (athlete, axis) observation pair:
    elapsed_days         = observed_at(obs2) - observed_at(obs1)
    predicted_from_state = obs1 normalized to [0, 1]   (the anchored state)
    squared_residual     = (obs2_[0,1] - predicted_from_state)^2   <- TARGET
  Values are scaled 0-100 -> [0, 1] so variance is in the engine's state units,
  directly comparable to confidence_process_noise_per_day / confidence_measured_variance.

LEAKAGE HANDLING (FORBIDDEN -- see build_training_frame.FORBIDDEN_FEATURES)
  * observation 2's own value as predicted_from_state (drives the residual to 0 --
    the target predicting itself); the prediction uses ONLY observation 1.
  * any observation at t+2 or later -- post-outcome for the (obs1->obs2) interval.
  * observation 2's raw_value -- encodes the normalized value the target is built from.
  * observation 2's observation_weight / measurement variance -- realized WITH the
    outcome; using it to set predicted variance leaks the measurement-noise term.
  * observation 2's timestamp into the predicted STATE mean -- only elapsed_days is a
    legitimate predictor of variance.
  Split integrity: athletes are held out as WHOLE GROUPS (grouped_split) so a single
  latent random-walk (whose successive residuals are correlated) never straddles the
  fit and the calibration check; rows keep per-athlete time order.

MODEL
  Per-axis ordinary least squares (numpy). NO gradient boosting, NO deep learning.
  learned q = clip(slope, 0, 0.5). The slope t-statistic is the signal-significance
  guardrail. Fit on held-in athletes; calibration measured on held-out athletes.

GATE (evaluate.py) -- promote OUT of shadow only if ALL hold
  * enough holdout pairs (MIN_HOLDOUT_PAIRS),
  * genuine elapsed-days signal: median slope t >= MIN_SLOPE_T AND median learned
    noise >= MIN_LEARNED_NOISE  (this is what fails honestly on a no-signal source,
    where the true q is 0 and the fitted slope is not significant),
  * learned noise calibrates the reliability diagram better than the default by
    >= MIN_CALIB_IMPROVEMENT on the holdout,
  * every learned noise within sane bounds.

WHY shadow_only + UNWIRED
  Synthetic magnitudes, and -- unlike the recovery-clearance beta -- ADR-0036's
  process-noise is not consumed by any parameter-override loader. The artifact's
  ``target`` binding to EngineParameters.confidence_process_noise_per_day is recorded
  as ``applied: false / binding: unwired``: it documents the calibration for a future
  promotion path and CANNOT change a live decision.

LIMITATIONS
  * Synthetic data -- estimator/gate demonstration, not calibrated magnitudes.
  * Successive-difference reconstruction assumes a full-weight benchmark anchors the
    state; partial-weight observations dilute the anchor and would need the
    observation_weight-scaled R modeled explicitly.
  * Pure random-walk latent (no mean reversion / ceiling clamp) -- a bounded axis near
    its ceiling would show sub-linear variance growth this linear fit ignores.
  * Population calibration, not per-athlete process-noise.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
