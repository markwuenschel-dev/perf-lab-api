"""Model card for the Q6 deload-need model (``q6_deload_priors_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q6 DELOAD-NEED — MODEL CARD (q6_deload_priors_v1)
=================================================

WHAT THIS IS
  A CALIBRATED probability
    P(a deload is needed within the next k days | today's deload-risk state)
  learned offline by a regularized logistic model and emitted as a versioned JSON
  artifact. It is designed to AUGMENT/REPLACE the hand-set DeloadNeed.score produced by
  the rule-based app.logic.deload_need.compute_deload_need. These are weak POPULATION
  priors — one aggregate response, reused across athletes — NOT per-athlete personalization.

DATA SOURCE
  Production-equivalent DB path: app.analysis.feature_builders.deload_risk_features
  .build_dataset (prescription_decisions joined to session_feedback outcomes).
  Runnable/testable path: a synthetic per-(athlete, day) fixture
  (build_training_frame.synthetic_deload_rows) with a PLANTED, autocorrelated deload-risk
  signal, so the whole pipeline runs and is tested without Postgres.

  *** SYNTHETIC / THIN-OUTCOME CAVEAT ***
  Real first-party deload outcomes are thin. The synthetic fixture is good only for
  exercising the pipeline and learning the SHAPE of the response, not calibrated effect
  magnitudes. That is the central reason the artifact is shadow_only.

LABEL
  Forward-looking binary: for athlete a on day t, deload_needed(t) = 1 if a deload event
  occurred on any of days t+1 .. t+k (k = HORIZON_DAYS = 7), else 0. The trailing k days of
  each athlete's series (truncated forward window) are dropped. A day's daily deload flag
  is captured from outcomes (session cancelled/deloaded, pain/soreness feedback,
  followed_as_prescribed=false), now recordable via SessionFeedback / telemetry.

FEATURES (all pre-outcome for day t)
  * fatigue_mean, fatigue_max         — current fatigue levels (fatigue axes).
  * mean_fatigue_slope, tissue_slope  — trailing 7-day OLS slopes (accumulating trend).
  * tissue_max                        — current tissue load.
  * adherence                         — recent adherence (lower = more risk).
  * perf_residual_slope               — trailing slope of the performance-decrement residual.
  * q1_decrement                      — the Q1 next-session decrement residual, reused
      read-only (app.ml.q1_decrement): observed session RPE minus its expectation given the
      planned load = performed worse than the plan should have cost = carried fatigue.
  * q2_recovery_deficit               — the Q2 recovery-clearance residual (per-athlete
      demeaned overnight clearance), reused read-only (app.ml.q2_recovery) and NEGATED so
      higher = recovering worse than the athlete's own baseline.
  These are exactly the signals compute_deload_need consults, plus the two learned
  residuals — which is precisely why Q1 and Q2 were built.

LEAKAGE HANDLING (features explicitly FORBIDDEN)
  * deload_event: the raw daily flag; the label is its forward (t+1..t+k) aggregate — a
    direct leak.
  * any fatigue/tissue/adherence/recovery value dated INSIDE the t+1..t+k horizon: measured
    during/after the window being predicted — post-outcome.
  * the label itself.
  Slopes are TRAILING only (window ends at t; today + past, never the future). Split
  integrity: athletes are held out as WHOLE GROUPS (grouped CV / grouped holdout) and rows
  keep per-athlete time order, so the within-athlete residuals, trailing slopes, and
  feature standardization never leak across train/validation. The one bounded approximation
  (shared with Q2) is that the per-athlete demeaning constant behind q2_recovery_deficit
  uses that athlete's whole in-partition series; because athletes are held out wholly it
  never crosses the split.

MODEL
  scikit-learn LogisticRegression (L2). Inverse-strength C selected by athlete-grouped
  K-fold CV on validation AUC. Features standardized (train stats stored in the artifact
  for reproducible scoring). NO gradient boosting, NO deep learning.

PROMOTION GATE (evaluate.py)
  Fit on held-in athletes, score whole held-out athletes vs the RULE baseline (a score
  mirroring compute_deload_need's drivers). Promote only if ALL hold: AUC improvement over
  the rule >= MIN_AUC_IMPROVEMENT, Brier improvement >= MIN_BRIER_IMPROVEMENT, decile
  calibration error <= MAX_CALIBRATION_ERROR, and the sparse-athlete subgroup is no worse
  (within SPARSE_TOLERANCE). Otherwise the verdict is stay_shadow, with reasons.

WHY shadow_only
  Synthetic/thin outcomes + an unvalidated learned response. The artifact is emitted
  offline and is NOT wired: the plug-in target is DeloadNeed.score in
  app.logic.deload_need (see the artifact's `target` block), but nothing in this pipeline
  applies it to a live decision. compute_deload_need itself already runs shadow_only and
  never hard-blocks training; this model would enter the same shadow slot first.

PROMOTION REQUIRES
  First-party deload outcomes — real day-level deload events and their antecedent risk
  states, now capturable via SessionFeedback (status / pain_flag / soreness_flag /
  followed_as_prescribed) and telemetry. Until enough accumulate to pass the gate on REAL
  (not synthetic) data, the model stays shadow_only.

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes; the base rate is fixture-set.
  * Population priors, not personalization: no per-athlete coefficients.
  * Weak-population-priors caveat: with thin outcomes the learned coefficients are
    low-confidence and must not be read as causal effect sizes.
  * The daily deload flag is itself a proxy assembled from feedback; the forward label
    inherits its noise.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
