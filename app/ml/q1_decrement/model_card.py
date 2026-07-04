"""Model card for the Q1 next-session-decrement predictor (``q1_decrement_v1``).

Kept as source so the provenance ships with the training code and can be printed by the
``__main__`` reproduction path in ``train``.
"""
from __future__ import annotations

MODEL_CARD = """\
Q1 NEXT-SESSION DECREMENT — MODEL CARD (q1_decrement_v1)
=======================================================

WHAT THIS IS
  An OFFLINE, shadow/research-only predictor of a next-session performance DECREMENT. It
  learns, from pre-next-session signals, how much HARDER than planned the next session
  will feel — i.e. accumulated fatigue that the plan did not account for. It is two
  stacked regularized linear models (scikit-learn Ridge); NO gradient boosting, NO deep
  learning. There is NO engine plug-in (a decrement maps to no single engine parameter),
  so the artifact is emitted shadow_only and applied to nothing live.

LABEL — WHY A RESIDUAL, NOT RAW next_rpe
  Raw next-session RPE conflates plan difficulty with decrement: a bigger prescribed load
  earns a higher RPE regardless of fatigue. We want only the "harder than it should have
  been" part. So:
    1. Fit an EXPECTATION model  E[next_rpe | planned next-session difficulty]  (ridge over
       the prescribed next-session duration/volume + a modality-change flag).
    2. decrement = observed_next_rpe - expected_next_rpe.
  A POSITIVE residual = the athlete reported more effort than the planned load should have
  cost = the session was harder than it should have been = a performance decrement /
  carried fatigue. Learning the residual (not raw RPE) isolates fatigue from plan
  difficulty; a raw-RPE label would just relearn "big planned session -> big RPE".

FEATURES
  Expectation model (planned difficulty, all PRE-outcome — the plan is known in advance):
    z_next_duration, z_next_volume, modality_change.
  Decrement PREDICTOR (all strictly PRE the next session):
    z_prev_rpe, z_prev_duration, z_prev_volume,
    z_prev_load  (z-scored prev_rpe * prev_duration — the session-stress / fatigue proxy),
    z_time_gap   (recovery gap; a short gap after a heavy session is the classic
                  "not recovered yet" signature),
    modality_change.
  All continuous features are z-scored WITHIN each athlete (removes cross-athlete scale;
  never mixes across the grouped split). Missing / degenerate -> 0.0 (neutral).

LEAKAGE HANDLING (features explicitly FORBIDDEN as PREDICTOR inputs)
  * next_rpe — the observed outcome; the label is built from it (direct leak).
  * expected_next_rpe — the expectation term the label is built from.
  * decrement — the supervised target itself.
  * any MEASURED next-session outcome other than its PRESCRIBED load (post-outcome).
  The ONLY next-session information used anywhere is the PLANNED difficulty
  (next_duration_minutes / next_volume_load / next_modality), and it feeds only the
  expectation model, never the predictor. Split integrity: whole athletes are held out
  (grouped time split), within-athlete z-scores stay inside one partition, and the
  expectation that defines the label is REFIT on the train partition only before the gate
  scores held-out athletes — so a held-out athlete never informs its own label.

DATA SOURCE
  Production-equivalent: app.analysis.feature_builders.session_decrement.build_dataset,
  which self-joins workout_logs (wl2 within 7 days after wl1, per athlete) and exports the
  session-pair columns (prev/next rpe, prev/next duration + volume, modality, time gap).
  Real workout-pair data is thin, so the pipeline is built + validated on a SYNTHETIC
  session-pair fixture. SYNTHETIC => SHAPE / weak-signal only, not calibrated magnitudes.

MODEL + GATE
  Ridge for both stages; the predictor's alpha is chosen by athlete-grouped K-fold CV.
  The promotion gate (evaluate.py) mirrors Q2: MAE improvement over a neutral predict-zero
  baseline ("no decrement beyond plan"), directional sign accuracy, decile calibration,
  sparse-athlete no-worse, and a saturation guard (no implausibly large predicted
  decrements). Verdict is promote only if every guardrail passes, else stay_shadow.

WHERE IT WOULD FEED LATER (not wired)
  A validated decrement is a SHADOW SIGNAL into the prescriber's expected-difficulty /
  readiness path: when the predictor expects the next session to run hotter than its plan,
  the prescriber could pre-emptively shade difficulty down or raise the readiness caution.
  It does NOT map to one engine parameter, which is exactly why it stays an offline
  research signal rather than a parameter override until real outcomes validate it.

LIMITATIONS
  * Synthetic data — shape only, not calibrated magnitudes.
  * RPE is the only performance proxy available in the pair export; a true velocity/load
    decrement would be a stronger label once instrumented.
  * Population model, not per-athlete personalization.
  * The expectation is a coarse linear plan->RPE map; anything non-linear in plan
    difficulty leaks into the residual as apparent "decrement".
"""


def model_card() -> str:
    """Return the model card text."""
    return MODEL_CARD
