"""Model card for the Q8 scoring-weight artifact (``q8_scoring_weights_v1``).

Kept as source so provenance ships with the training code and prints from the ``__main__``
reproduction path in ``train`` / ``evaluate``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q8 SCORING-WEIGHT CALIBRATION — MODEL CARD (q8_scoring_weights_v1)
=================================================================

WHAT THIS IS
  An OFFLINE-learned weight vector over the eight candidate scoring axes
    goal_alignment, state_fit, fatigue_penalty, tissue_penalty,
    novelty_bonus, habit_bonus, template_bias, weak_point_coverage
  that ranks prescription candidates toward GOOD athlete outcomes better than the
  production DEFAULT_SCORE_WEIGHTS. Emitted as a shadow_only, ScoreWeightProfile-shaped
  JSON payload. This is offline POLICY EVALUATION / learning-to-rank, NOT per-athlete
  personalization and NOT a live change to prescription.

DATA SOURCE
  candidate_decision_logs (every considered candidate, chosen AND rejected) with its
  per-candidate score_components_json, joined via the prescription decision's planned
  session to session_feedback (status, satisfaction_score, followed_as_prescribed,
  pain_flag). DB reader: app.analysis.feature_builders.scoring_weight_features.build_dataset.

  *** SYNTHETIC CAVEAT ***
  Real candidate-log + feedback pairs are only just being captured. The pipeline runs and
  is tested on a synthetic fixture (build_training_frame.synthetic_decision_rows) with a
  planted "state_fit matters" signal. It is good only for exercising the SHAPE of the
  learning-to-rank + gate, never for shipping magnitudes — the central reason for
  shadow_only.

LABEL
  Pairwise ranking target. For each decision with a logged chosen candidate and an observed
  outcome, and for each rejected candidate r:
    feature(pair) = components(chosen) - components(r)      (over the 8 axes)
    pair_label    = 1  iff the chosen led to a GOOD outcome (chosen SHOULD outrank r)
                  = 0  otherwise                            (r should have won)
  GOOD outcome = collapsed feedback quality >= 0.60 (satisfaction 1..5 -> 0..1, blended
  with completion status + adherence, minus a pain penalty; see
  scoring_weight_features.outcome_score).

FEATURES (the 8 score components ONLY)
  The raw per-candidate score components. A no-intercept logistic on the pairwise
  differences (Bradley-Terry / RankNet) makes the coefficient vector itself a score-weight
  vector: P(chosen outranks r) = sigmoid(w . (comp_chosen - comp_r)).

LEAKAGE HANDLING (columns explicitly FORBIDDEN as features)
  * final_score: the current policy's composite of the very weights being relearned — using
    it just re-derives DEFAULT_SCORE_WEIGHTS. Never a feature.
  * the chosen flag: the current policy's argmax DECISION. It defines the ranking pairs and
    which candidate's outcome was observed (label structure) — never a per-candidate input.
  * status / satisfaction_score / followed_as_prescribed / pain_flag: the OUTCOME (label).
  * hard_failed: a hard-constraint filter flag; such candidates bypass scoring and are
    dropped from the ranked pool.
  Split integrity: whole DECISIONS are held out as groups (a decision is the ranking group),
  so no group's outcome leaks across train/test.

MODEL & GUARDRAILS
  Regularized linear only (scikit-learn LogisticRegression, no intercept, strong L2).
  NO gradient boosting, NO deep learning. Raw coefficients are rescaled to the L1 norm of
  DEFAULT_SCORE_WEIGHTS (scores land in the engine's expected range; ranking unchanged),
  then PROJECTED onto the production safety box and ROUND-TRIPPED through
  candidate.validate_score_weights + wrapped in a candidate.ScoreWeightProfile:
    * fatigue_penalty <= -0.05 and tissue_penalty <= -0.02 (stay penalising),
    * novelty_bonus, habit_bonus in [0, 0.10].
  train() re-runs validate_score_weights and raises if any violation remains, so the
  emitted weights provably satisfy the same guardrails the engine enforces.

PROMOTION GATE (evaluate.py)
  Primary metric Q(w) = P(w top-1's the chosen candidate | good decision)
                      - P(w top-1's the chosen candidate | bad  decision).
  Because the logging (default) policy is the source of the chosen argmax, Q(default) ~ 0 by
  construction; a learned vector must reach Q >= 0.10 AND beat the default by >= 0.10, over
  >= 30 held-out decisions that contain both good and bad outcomes, with zero guardrail
  violations. Secondary: a replay/direct off-policy value V(w) = mean outcome over decisions
  where argmax score(w) == chosen. Pure noise -> stay_shadow (verified in tests).

WHY shadow_only / BINDING
  Synthetic magnitudes + first-party outcome capture only just wired. The artifact is
  shadow_only and is NOT wired into app.logic.prescriber. When promoted, the binding is:
  pass the learned ScoreWeightProfile.weights as the weights= argument of
  app.logic.constraint_engine.candidate.score_candidate(candidate, weights=...).

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes.
  * Promotion needs enough REAL candidate-log + feedback pairs (capture just wired) with a
    good/bad mix before the gate can honestly promote.
  * Counterfactual outcomes for rejected candidates are unobserved; the replay value only
    covers decisions where the learned argmax matches the logged choice.
  * One population weight vector — no per-athlete or per-context weights.
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
