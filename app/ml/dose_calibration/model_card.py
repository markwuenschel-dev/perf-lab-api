"""Model card for the dose-law calibration artifact (``dose_calibration_priors_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
DOSE-LAW CALIBRATION — MODEL CARD (dose_calibration_priors_v1)
=============================================================

WHAT THIS IS
  Weak POPULATION priors for the engine's session dose law
    V     = w_dur*duration + w_vol*volume_load + w_sets*sets
    D_k   = shape_six[modality][k] * base(V, I, Delta, N, F)
  learned offline and emitted as a versioned JSON artifact (an ``engine_overrides`` block)
  consumed by app.engine.parameter_overrides on its DOSE path. These are NOT per-athlete
  personalization — one aggregate population response is fit and reused across athletes.
  Only two dose fields are touched: ``dose_volume_weights`` and
  ``dose_shape_six_by_modality``.

DATA SOURCE
  Production-equivalent: the workout-logs table joined to each athlete's next logged
  session. No first-party dose CSV exists yet, so build_training_frame.synthesize_sessions
  emits a deterministic SYNTHETIC stand-in that keeps the pipeline runnable + testable
  without a DB.

  *** SYNTHETIC CAVEAT ***
  The synthetic source is good only for learning the SHAPE of weak priors, not effect
  magnitudes. That is the central reason the artifact is shadow_only.

LABEL (outcome proxy)
  Next-session ``session_rpe`` residual. For athlete a on session t with a genuine next
  session t+1 within MAX_SESSION_GAP_DAYS:
    label(t) = session_rpe(t+1) - mean_t' session_rpe(t')   (per-athlete residual)
  A residual (not a raw value) so the model learns each athlete's within-person dose->cost
  response rather than cross-athlete RPE-reporting styles. The dose law is "well calibrated"
  to the extent the modeled dose tracks this outcome. Non-consecutive sessions (a layoff has
  cleared the residual fatigue) produce no label.

FEATURES (all pre-outcome)
  The volume-proxy COMPONENTS the weights act on, population-standardized:
    f_duration  <- duration_minutes
    f_volume_load <- total_volume_load
    f_sets      <- estimated_sets (or the engine's duration fallback)
  Plus the engine's currently-modeled dose (via calculate_stress_dose) used for the
  per-modality shape calibration and the promotion gate.

LEAKAGE HANDLING (features explicitly FORBIDDEN)
  * any session-(t+1) field (RPE / duration / volume / sets / RIR of the next session):
    measured AFTER the dose being calibrated — post-outcome.
  * the modeled dose of session t+1.
  * the label itself.
  Split integrity: athletes are held out as WHOLE GROUPS (grouped CV / grouped holdout) and
  rows keep per-athlete time order, so the per-athlete residual never leaks across splits.

MODEL
  Regularized linear (scikit-learn Ridge). Alpha selected by athlete-grouped K-fold. NO
  gradient boosting, NO deep learning. The aggregate component coefficients become SMALL
  MULTIPLICATIVE NUDGES to the volume weights (<= +/-15%), and each modality's shape
  multipliers are nudged (<= +/-10%) by how well its modeled dose tracks the outcome
  relative to the pooled average. Every nudge is driven by a CLAMPED effect ratio, so a
  near-zero learned effect reproduces the current literature defaults exactly.

WHY shadow_only
  Synthetic magnitudes + unvalidated calibration. The loader (parameter_overrides.py)
  refuses to apply a shadow_only artifact on a production path (only the shadow evaluator
  passes allow_shadow=True), so these priors cannot change a live dose until validated and
  promoted. The committed artifact is additionally an untrained v0 placeholder equal to the
  engine defaults (a zero-change override).

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes.
  * Population priors, not personalization: no per-athlete weights.
  * Next-session RPE is a coarse residual-fatigue proxy and is confounded by the next
    session's own prescription; a true RPE-for-load label needs first-party load data.
  * Only volume + modality-shape weights are calibrated; the dose exponents
    (alpha/beta/gamma/rho) and floors are left at their literature defaults.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
