"""Model card for the Q2 recovery-priors artifact (``q2_recovery_priors_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train_recovery_priors``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q2 RECOVERY-PRIORS — MODEL CARD (q2_recovery_priors_v1)
=======================================================

WHAT THIS IS
  Weak POPULATION priors for the engine's fatigue-clearance recovery modifier
    m_a(z) = clip(exp(sum_k beta[a,k] * z_k), 0.6, 1.5)
  learned offline and emitted as a versioned JSON artifact consumed by
  app.engine.parameter_overrides. These are NOT per-athlete personalization — one
  aggregate population response is fit and reused across athletes and fatigue axes.

DATA SOURCE
  Primary: data/kaggle/google-fit-data/hamon_googlefit_medical_realistic.csv
  Per-(user_id, date) rows with hrv, resting_hr, sleep_hours, sleep_efficiency,
  fatigue_score. Signals used: sleep_hours, hrv, resting_hr.
  Production-equivalent DB path (not used here to stay Postgres-free + testable):
  app.analysis.feature_builders.fatigue_recovery over the wellness_samples table.

  *** SYNTHETIC CAVEAT ***
  The CSV is SYNTHETIC. It is good only for learning the SHAPE of weak priors, not
  effect magnitudes. That is the central reason the artifact is shadow_only: the
  learned hrv/rhr terms are staged for the shadow recovery service to validate against
  real outcomes before any promotion.

LABEL
  Next-day fatigue-clearance residual. For athlete a on day t with a genuine day-(t+1)
  successor:
    clearance(t) = fatigue_score(t) - fatigue_score(t+1)   (>0 = fatigue fell overnight)
    label(t)     = clearance(t) - mean_t' clearance(t')     (per-athlete residual)
  A residual (not a raw next-day value) so the model learns each athlete's within-person
  recovery response rather than cross-athlete fatigue levels. Non-consecutive days
  produce no label.

FEATURES (all pre-outcome)
  z_sleep, z_hrv, z_rhr — each the z-score of the raw signal against that athlete's
  trailing 28-day personal baseline, with the current day excluded from its own baseline
  (mirroring readiness_service's BASELINE_WINDOW_DAYS / "excludes before" intent).
  Missing signal or insufficient baseline history imputes to 0.0 (neutral = at-baseline).

LEAKAGE HANDLING (features explicitly FORBIDDEN)
  * fatigue_score(t) and fatigue_score(t+1): the label is built from them — direct leak.
  * any next-day (t+1) signal (hrv/rhr/sleep of day t+1): measured AFTER the recovery
    window being predicted — post-outcome.
  * cardiometabolic_risk_state and other downstream health-state outcomes.
  * the label itself.
  Split integrity: athletes are held out as WHOLE GROUPS (grouped CV / grouped holdout)
  and rows preserve per-athlete time order, so per-athlete baselines and the per-athlete
  residual never leak across train/validation.

MODEL
  Regularized linear (scikit-learn Ridge). Alpha selected by athlete-grouped K-fold.
  NO gradient boosting, NO deep learning. The aggregate coefficients are mapped to
  per-axis betas: sleep/stress keep their current engine defaults per axis; hrv/rhr are
  added by scaling each axis's default sleep weight by the learned effect ratio to sleep
  (capped to keep the priors weak). rhr's coefficient is expected negative (lower resting
  HR = better recovery).

WHY shadow_only
  Synthetic magnitudes + unvalidated new signals. The loader (parameter_overrides.py)
  refuses to apply a shadow_only artifact on a production path (only the shadow service
  passes allow_shadow=True), so these priors cannot change a live decision until
  validated and promoted.

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes.
  * No soreness or mood in the source, so those betas are left at 0.0.
  * Population priors, not personalization: no per-athlete coefficients.
  * fatigue_score is itself a synthetic composite; the clearance label inherits its
    idiosyncrasies.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
