"""Model card for the shadow-EKF calibration artifact (``q10_ekf_calibration_v1``).

Kept as source so provenance ships with the code and can be printed by the
``evaluate_ekf`` reproduction path.
"""
from __future__ import annotations

MODEL_CARD = """\
SHADOW-EKF CALIBRATION -- MODEL CARD (q10_ekf_calibration_v1)
============================================================

WHAT THIS IS
  The runnable calibration gate for the shadow full-covariance EKF (ADR-0041). It does
  NOT fit or promote anything -- it exercises the shipped estimator over a synthetic
  event stream and asks whether its joint covariance is well-calibrated, plus whether it
  tracks benchmarks at least as well as the production scalar path. Emitted as a
  versioned JSON artifact. Shadow-only; nothing here changes production behavior.

THE ESTIMATOR BEING CALIBRATED (ADR-0041)
  A full 22-dim EKF over S=(X,F,T). Transition model IS update_athlete_state
  (finite-difference linearized); benchmark update is Joseph-form in normalized per-axis
  space, reducing exactly to the production scalar residual anchor on a single axis while
  the full P also corrects correlated axes.

GENERATIVE MODEL (self-consistent, DB-free -- app.ml.q10_confidence.ekf_replay)
  Ground truth is a twin trajectory from a *true* baseline; both estimators start from a
  deliberately WRONG seed baseline and must converge from data. A benchmark on capacity
  axis k yields  realized = true_k + N(0, sqrt(R)),  R = effective_variance/mapping^2 --
  the SAME variance the filter assumes. Observations are left unclamped so the
  NIS/coverage relation stays exact. Calibration here validates the covariance
  bookkeeping (predict propagation, Joseph updates, PSD projection), not robustness to a
  mis-specified R.

CHECKS AND VERDICT
  * NIS (normalized innovation squared): aggregate total is chi-square with dof = sum
    n_obs; must fall inside a two-sided band AND the ratio NIS/dof in [0.6, 1.6].
  * Interval coverage: empirical 50/80/95% coverage vs nominal, ASYMMETRIC tolerance --
    under-coverage (overconfidence, the dangerous direction) penalized tightly;
    mild over-coverage (conservatism) tolerated.
  * EKF-vs-scalar one-step benchmark-prediction RMSE: REPORTED, not gated (scenario
    dependent); a negative margin is surfaced as a soft warning.
  Verdict is promote | stay_shadow. Promotion is OUT OF SCOPE for this arc -- the
  estimator stays shadow regardless; the verdict is the honest gate a future promotion
  would require.

PRODUCTION FEED
  The same NIS consistency check runs on real athlete data via
  app.analysis.feature_builders.ekf_calibration_features.summarize_ekf_shadow over
  ekf_shadow_log rows (coverage needs the predictive std, which only replay has).

REPRODUCE
  python -m app.ml.q10_confidence.evaluate_ekf
"""
