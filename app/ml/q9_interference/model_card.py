"""Model card for the Q9 interference suppression-alpha artifact (``q9_interference_priors_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q9 CONCURRENT-TRAINING INTERFERENCE — MODEL CARD (q9_interference_priors_v1)
===========================================================================

WHAT THIS IS
  An offline estimate of the ADR-0037 CROSS-AXIS interference suppression alphas. The
  engine models how concurrent load blunts a target adaptation with a smooth curve
    gain_efficiency(z) = floor + (1 - floor) * exp(-alpha * z)          [floor, 1.0]
  (app/logic/interference.py: suppression_exp / directional_interference_multiplier),
  where z is the INTERFERING load fraction in [0, 1+]:
    * endurance load (0.4*metabolic + 0.6*structural, /100) blunts max_strength / power /
      hypertrophy,
    * CNS fatigue (/100) blunts power / skill,
    * structural fatigue (/100) blunts aerobic quality.
  This pipeline LEARNS alpha per interference pair from data and compares it to the engine
  default, emitting a versioned JSON artifact recording learned-vs-default alphas. These
  are weak POPULATION priors — one aggregate alpha per pair, reused across athletes — NOT
  per-athlete personalization.

DATA SOURCE
  Production-equivalent DB path: app.analysis.feature_builders.interference_features
  .build_dataset — benchmark_observations joined to benchmark_definitions for domain +
  better_direction. Consecutive benchmarks in a strength/power/skill domain give a realized
  gain (delta normalized_value, oriented by better_direction); the concurrent interfering
  load over the interval between them is z.
  Runnable/testable path: a synthetic per-(athlete, block) fixture
  (build_training_frame.synthetic_interference_rows) with a PLANTED, known suppression
  alpha, so the whole pipeline runs without Postgres AND the fit can be checked for alpha
  recovery.

  *** SYNTHETIC CAVEAT ***
  The fixture is SYNTHETIC — good only for exercising the pipeline and validating that the
  constrained fit recovers a planted alpha, NOT for calibrated effect magnitudes. That is
  the central reason the artifact is shadow_only: a learned alpha is staged for validation
  against real benchmark outcomes before it could recalibrate the reviewed engine default.

LABEL
  gain_efficiency = realized_gain / expected_gain, a per-athlete, per-pair RATIO in
  (0, ~1.3]. expected_gain is the athlete's OWN mean realized gain over their
  zero-interference anchor blocks (z <= LOW_Z_ANCHOR). A ratio (not a raw post-block
  benchmark) so the model learns each athlete's within-person suppression response rather
  than cross-athlete gain magnitudes. Athletes without enough anchors, or with a
  non-positive expected gain, are dropped.

FEATURE (pre-outcome)
  z_interfering_load — the concurrent interfering-domain load fraction (interfering load /
  100) that PRECEDES / spans the block, measured before the post-block benchmark. It is the
  same load proxy the engine feeds into suppression_exp.

LEAKAGE HANDLING (features explicitly FORBIDDEN)
  * realized_gain / the post-block benchmark value: measured AFTER the interference window —
    the outcome, not an input.
  * expected_gain: the per-athlete zero-interference normalizer, derived from outcomes.
  * better_direction: orientation metadata used to sign the delta; folding it in as a
    feature would leak the label's sign.
  * interfering load from a DIFFERENT block.
  * the label itself.
  Split integrity: athletes are held out as WHOLE GROUPS (grouped holdout) and blocks keep
  per-athlete order, so the per-athlete zero-interference normalizer never crosses the
  train/test split.

MODEL
  A CONSTRAINED, REGULARIZED nonlinear least-squares fit of alpha to the suppression curve
  with the per-axis floor FIXED at the engine's interference_floor_by_axis value: minimize
  sum_i (floor + (1-floor)*exp(-alpha*z_i) - eff_i)^2 + l2*(alpha - alpha_default)^2, with
  alpha bounded to [0, ALPHA_MAX]. The ridge term is a weak pull toward the reviewed engine
  default so a thin/noisy slice does not swing alpha wildly. NO gradient boosting, NO deep
  learning.

PROMOTION GATE (evaluate.py)
  Per pair: fit alpha on held-in athletes, then on whole held-out athletes compare the
  learned alpha to the engine DEFAULT alpha by MAE of predicted gain_efficiency. Promote a
  pair only if ALL hold:
    * MAE improvement (default - learned) >= MIN_MAE_IMPROVEMENT,
    * a real suppression signal is present (test corr(z, efficiency) <= -MIN_SUPPRESSION_CORR),
    * the ADR-0037 INTERFERENCE-FLOOR GUARDRAIL: the learned curve must keep strong
      suppression at high concurrent load — efficiency at z = Z_REF (=1.0) must be
      <= MAX_EFFICIENCY_AT_REF (0.80). A learned alpha too SMALL implies interference has
      almost no effect, which would implausibly weaken the reviewed interference floor; the
      gate refuses to promote it (echoes the C4 recalibration finding that a naive unified
      exponential fit drifts to ~0.87 > 0.80 and guts the guardrail),
    * learned alpha within a plausible band [ALPHA_MIN_PLAUSIBLE, ALPHA_MAX],
    * the sparse-athlete subgroup is no worse (within SPARSE_TOLERANCE).
  Otherwise the verdict is stay_shadow, with reasons. On the NO-SIGNAL null (effect=0) the
  fit collapses to alpha ~= 0 (a flat curve), which fails the floor guardrail and the
  suppression-signal check — the honest stay_shadow.

WHY shadow_only / THE (UNWIRED) BINDING
  Synthetic magnitudes + an unvalidated learned alpha. The artifact's `target` block names
  the binding: each pair maps to an EngineParameters.interference_*_alpha field
  (interference_e_on_strength_alpha, interference_e_on_power_alpha,
  interference_cns_on_power_alpha, interference_cns_on_skill_alpha,
  interference_structural_on_endurance_quality_alpha). NOTHING in this pipeline applies an
  override: unlike the Q2 recovery artifact there is no parameter_overrides schema for the
  interference alphas, so the learned value is recorded for review ONLY and cannot change a
  live decision until validated on real outcomes and promoted.

PROMOTION REQUIRES
  Real benchmark outcomes with observed concurrent interfering load (the DB feature-builder
  path), enough to pass the gate on REAL (not synthetic) data without weakening the ADR-0037
  interference-floor guardrail.

LIMITATIONS
  * Synthetic data — shape / recovery check only, not calibrated magnitudes.
  * Population priors, not personalization: one alpha per pair.
  * The floor is held FIXED at the engine default; this pipeline calibrates alpha only, so
    it cannot (by construction) lower the safety floor — it can only be vetoed for trying to
    weaken effective suppression via too-small an alpha.
  * expected_gain is a per-athlete anchor mean; a thin anchor set inflates its variance and
    the label inherits that noise.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
