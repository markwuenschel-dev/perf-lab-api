"""Train Q8 scoring weights: offline learning-to-rank the candidate score-weight vector.

Fits a REGULARIZED LINEAR model — a no-intercept logistic on pairwise
``components(chosen) - components(rejected)`` differences (a Bradley-Terry / RankNet
ranker) — so the learned coefficient vector is directly a score-weight vector over the
eight axes. The raw coefficients are then:

1. rescaled to the L1 magnitude of ``DEFAULT_SCORE_WEIGHTS`` (so learned scores live in the
   same range the engine expects), and
2. PROJECTED onto the production safety box and ROUND-TRIPPED through
   ``candidate.validate_score_weights`` / wrapped in a ``ScoreWeightProfile`` — the learned
   weights provably satisfy the same guardrails the engine enforces.

The emitted artifact is ``shadow_only`` and is NOT wired into the prescriber. Binding, for
whenever it is promoted: pass ``profile.weights`` as the ``weights=`` argument of
``app.logic.constraint_engine.candidate.score_candidate`` (see ``model_card``).

Run ``python -m app.ml.q8_scoring.train`` for a synthetic-fixture reproduction.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from app.analysis.feature_builders.scoring_weight_features import SCORE_AXES
from app.logic.constraint_engine.candidate import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeightProfile,
    validate_score_weights,
)
from app.ml.q8_scoring.build_training_frame import AXES, PAIR_LABEL, build_pairwise_frame
from app.ml.q8_scoring.model_card import MODEL_CARD

ARTIFACT_VERSION = "q8_scoring_weights_v1"
NAMESPACE = "q8_scoring"
PROFILE_VERSION = "v1-shadow"

# Inverse L2 strength for the pairwise logistic (small C = strong regularization -> weak,
# noise-resistant weights, matching the "keep it a simple regularized linear model" intent).
_LOGREG_C = 0.5

# Projection targets that mirror candidate._WEIGHT_CONSTRAINTS. validate_score_weights is the
# source of truth; train() re-checks and raises if these ever drift from the validator.
_FATIGUE_MAX = -0.05   # fatigue_penalty must stay <= this (negative = penalising)
_TISSUE_MAX = -0.02    # tissue_penalty must stay <= this
_BONUS_MIN, _BONUS_MAX = 0.0, 0.10  # novelty_bonus / habit_bonus box

assert set(AXES) == set(DEFAULT_SCORE_WEIGHTS), "AXES drifted from DEFAULT_SCORE_WEIGHTS"


def fit_raw_weights(pairwise: pd.DataFrame) -> dict[str, float]:
    """Fit the no-intercept pairwise logistic; return raw per-axis coefficients.

    ``P(chosen outranks rejected) = sigmoid(w . (comp_chosen - comp_rejected))``. With a
    degenerate label (only good or only bad decisions survive) there is no ranking signal,
    so we fall back to the default weights (-> the gate will see zero improvement).
    """
    y = pairwise[PAIR_LABEL].to_numpy(dtype=int)
    if pairwise.empty or np.unique(y).size < 2:
        return dict(DEFAULT_SCORE_WEIGHTS)
    x = pairwise[list(AXES)].to_numpy(dtype=float)
    model = LogisticRegression(fit_intercept=False, C=_LOGREG_C, max_iter=1000)
    model.fit(x, y)
    coefs = model.coef_[0]
    return {axis: float(c) for axis, c in zip(AXES, coefs, strict=True)}


def _rescale_to_default_l1(raw: dict[str, float]) -> dict[str, float]:
    """Scale the raw coefficient vector to the L1 norm of the default weights.

    Logistic coefficients are only defined up to the arbitrary scale absorbed by the
    sigmoid; rescaling to ``sum|DEFAULT_SCORE_WEIGHTS|`` puts learned scores in the range
    ``score_candidate`` expects without changing the induced ranking.
    """
    default_l1 = sum(abs(v) for v in DEFAULT_SCORE_WEIGHTS.values())
    raw_l1 = sum(abs(v) for v in raw.values())
    scale = (default_l1 / raw_l1) if raw_l1 > 1e-12 else 0.0
    return {axis: raw.get(axis, 0.0) * scale for axis in AXES}


def _project_to_guardrails(weights: dict[str, float]) -> dict[str, float]:
    """Project onto the production safety box so validate_score_weights passes.

    Penalty axes are clamped to stay negative (a wrong-signed learned penalty is snapped to
    the constraint boundary); the two bonus axes are clipped into ``[0, 0.10]``.
    """
    proj = {axis: float(weights.get(axis, DEFAULT_SCORE_WEIGHTS[axis])) for axis in AXES}
    proj["fatigue_penalty"] = min(proj["fatigue_penalty"], _FATIGUE_MAX)
    proj["tissue_penalty"] = min(proj["tissue_penalty"], _TISSUE_MAX)
    proj["novelty_bonus"] = float(np.clip(proj["novelty_bonus"], _BONUS_MIN, _BONUS_MAX))
    proj["habit_bonus"] = float(np.clip(proj["habit_bonus"], _BONUS_MIN, _BONUS_MAX))
    return {axis: round(v, 4) for axis, v in proj.items()}


def learn_profile(pairwise: pd.DataFrame) -> ScoreWeightProfile:
    """Fit -> rescale -> project -> validate, returning a guardrail-safe ScoreWeightProfile."""
    raw = fit_raw_weights(pairwise)
    projected = _project_to_guardrails(_rescale_to_default_l1(raw))
    violations = validate_score_weights(projected)
    if violations:  # the projection should make this impossible; fail loudly if constraints drift
        raise ValueError(f"learned weights failed validate_score_weights: {violations}")
    return ScoreWeightProfile(weights=projected, version=PROFILE_VERSION)


def build_artifact(
    profile: ScoreWeightProfile,
    *,
    raw: dict[str, float] | None = None,
    n_pairs: int = 0,
    n_decisions: int = 0,
    source: str = "synthetic:planted-state_fit",
) -> dict[str, Any]:
    """Assemble the shadow-only, ScoreWeightProfile-shaped artifact payload."""
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "profile_version": profile.version,
        "weights": dict(profile.weights),
        "binding": "score_candidate(candidate, weights=<weights>) — shadow/offline only; "
        "NOT wired into app.logic.prescriber",
        "training": {
            "model": "pairwise no-intercept logistic (Bradley-Terry / RankNet)",
            "C": _LOGREG_C,
            "axes": list(AXES),
            "raw_coefficients": raw or {},
            "n_pairs": n_pairs,
            "n_decisions": n_decisions,
            "label": "pairwise: chosen should outrank rejected iff chosen led to a GOOD outcome",
            "leakage": "features are the 8 score components only; final_score & the chosen "
            "flag are never features (they encode the current policy).",
            "note": "Synthetic source: SHAPE/weak-signal only. See model_card.MODEL_CARD.",
        },
    }


def train(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """End-to-end: DB-/fixture-shaped rows -> guardrail-safe shadow-only artifact."""
    from app.ml.q8_scoring.build_training_frame import build_tidy_frame

    tidy = build_tidy_frame(rows)
    pairwise = build_pairwise_frame(tidy)
    raw = fit_raw_weights(pairwise)
    profile = learn_profile(pairwise)
    return build_artifact(
        profile,
        raw=raw,
        n_pairs=int(len(pairwise)),
        n_decisions=int(tidy["decision_id"].nunique()) if not tidy.empty else 0,
    )


def main() -> None:
    from app.ml.q8_scoring.build_training_frame import synthetic_decision_rows

    rows = synthetic_decision_rows(planted=True, seed=0)
    artifact = train(rows)
    print(MODEL_CARD)
    print(f"\nlearned weights pass validate_score_weights: {validate_score_weights(artifact['weights']) == []}")
    print(json.dumps(artifact, indent=2))
    _ = SCORE_AXES  # provenance: axes come from the shared feature-builder manifest


if __name__ == "__main__":
    main()
