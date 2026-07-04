"""Model card for the Q3 tissue-risk model (``q3_tissue_priors_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q3 TISSUE-RISK — MODEL CARD (q3_tissue_priors_v1)
=================================================

WHAT THIS IS
  A CALIBRATED per-axis probability
    P(a tissue/pain event at this axis within the next k days | today's exposure state)
  learned offline by a regularized logistic model and emitted as a versioned JSON
  artifact. It is designed to REPLACE the hand-set, currently-uncalibrated per-axis scores
  in app.logic.tissue_risk.TissueRiskPrediction.risk_by_axis (produced by the rule-based
  compute_tissue_risk, which returns calibrated=False). These are weak POPULATION priors —
  one aggregate response pooled across athletes AND axes — NOT per-athlete personalization.

DATA SOURCE
  Production-equivalent DB path: app.analysis.feature_builders.tissue_risk_features
  .build_dataset — outcome_events of tissue type (tissue_skip, tissue_modified, pain_event,
  non_tissue_skip, unknown_skip), per (athlete, occurred_at, tissue_axis), LEFT JOINed to
  same-day wellness_samples (sleep_quality, soreness, hrv_ms). That builder is SQL-only and
  pandas-free (it sits under the strict pyright gate); this pipeline does NOT modify it and
  keeps all pandas/numpy under app/ml (excluded from the strict gate).
  Runnable/testable path: a synthetic per-(athlete, day, axis) fixture
  (build_training_frame.synthetic_tissue_rows) with a PLANTED, autocorrelated tissue-risk
  signal, so the whole pipeline runs and is tested without Postgres.

  *** SYNTHETIC / THIN-OUTCOME CAVEAT ***
  Real first-party tissue outcomes are thin. The synthetic fixture is good only for
  exercising the pipeline and learning the SHAPE of the response, not calibrated effect
  magnitudes. That is the central reason the artifact is shadow_only.

LABEL
  Forward-looking binary: for athlete a, axis x, day t, tissue_event(t) = 1 if a tissue
  event occurred at axis x on any of days t+1 .. t+k (k = HORIZON_DAYS = 7), else 0. The
  trailing k days of each (athlete, axis) series (truncated forward window) are dropped.

FEATURES (all pre-outcome for day t; mirror compute_tissue_risk's inputs + fatigue)
  * tissue_load     — current accumulated tissue stress at the axis (0..100). Rule's
      base_risk driver (state.tissue_t.<axis>).
  * acute_exposure  — 7-day trailing cumulative tissue dose (acute exposure magnitude).
  * acwr            — acute:chronic ratio = d7 / (d28 / 4). The rule's ACWR spike driver
      (risk once > 1.3), built from the 3d/7d/28d lagged exposures.
  * concentration   — d3 / d7 (how concentrated the acute dose is in the last 3 days).
      The rule's recent-concentration driver.
  * prior_pain      — 1.0 if a tissue event hit this axis in the trailing PRIOR_PAIN_DAYS,
      read STRICTLY-PAST (shift(1) before the window). The rule's prior-pain bump.
  * fatigue         — same-day fatigue level. The rule IGNORES this; it is the learned
      model's edge over the rule.

LEAKAGE HANDLING (features explicitly FORBIDDEN)
  * tissue_event: the raw daily flag; the label is its forward (t+1..t+k) aggregate — a
    direct leak.
  * any tissue-load / exposure / pain flag dated INSIDE the t+1..t+k horizon: measured
    during/after the window being predicted — post-outcome.
  * the same-day (t) pain flag as a feature — it is part of the outcome stream; only
    strictly-past (<= t-1) events set prior_pain.
  * the label itself.
  Exposures are TRAILING cumulative sums (window ends at t; today + past, never the future).
  Split integrity: athletes are held out as WHOLE GROUPS (grouped CV / grouped holdout) so
  every axis of an athlete stays on one side of the split, and rows keep per-(athlete, axis)
  time order — the trailing exposures, prior-pain flag and feature standardization never
  leak across train/validation.

MODEL
  scikit-learn LogisticRegression (L2). Inverse-strength C selected by athlete-grouped
  K-fold CV on validation AUC. Features standardized (train stats stored in the artifact for
  reproducible scoring). One pooled model scores every (athlete, day, axis) row, so each
  prediction IS the per-axis probability. NO gradient boosting, NO deep learning.

PROMOTION GATE (evaluate.py)
  Fit on held-in athletes, score whole held-out athletes vs the RULE baseline (a score
  mirroring compute_tissue_risk's base/ACWR-spike/concentration/prior-pain drivers). Promote
  only if ALL hold: AUC improvement over the rule >= MIN_AUC_IMPROVEMENT, Brier improvement
  >= MIN_BRIER_IMPROVEMENT, decile calibration error <= MAX_CALIBRATION_ERROR, and the
  sparse-athlete subgroup is no worse (within SPARSE_TOLERANCE). Otherwise the verdict is
  stay_shadow, with reasons.

WHY shadow_only / UNWIRED
  Synthetic/thin outcomes + an unvalidated learned response. The artifact is emitted offline
  and is NOT wired: NO parameter override is applied and nothing reads it on a live path. The
  plug-in target is TissueRiskPrediction.risk_by_axis in app.logic.tissue_risk (see the
  artifact's `target` block); compute_tissue_risk itself already runs shadow_only
  (Level 0: log) with calibrated=False, and this model would enter that shadow slot first —
  flipping risk_by_axis to a calibrated probability only AFTER it passes the gate on REAL
  (not synthetic) outcomes.

PROMOTION REQUIRES
  First-party tissue outcomes — real per-axis tissue events (tissue_skip / tissue_modified /
  pain_event) and their antecedent exposure states, accumulated in outcome_events. Until
  enough pass the gate on REAL data, the model stays shadow_only.

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes; the base rate is fixture-set.
  * Population priors pooled across axes, not per-axis or per-athlete coefficients; a busy
    axis (e.g. shoulder for an overhead athlete) is not modeled distinctly.
  * The daily tissue-event flag is itself a proxy assembled from outcome_events; the forward
    label inherits its noise and any event-attribution error.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
