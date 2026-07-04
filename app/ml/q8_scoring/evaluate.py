"""Offline validation gate for Q8 scoring weights (promotion out of shadow).

Decides whether the LEARNED weight vector ranks candidates toward GOOD outcomes better than
``DEFAULT_SCORE_WEIGHTS``, on held-out decisions, under promotion guardrails.

PRIMARY METRIC — outcome-weighted top-1 agreement:

    Q(w) = P(w top-1's the chosen candidate | GOOD decision)
         - P(w top-1's the chosen candidate | BAD  decision)

A good weight vector reproduces the choice when it turned out well and diverges from it when
it turned out badly, so ``Q in [-1, 1]`` is high; it is class-balanced (robust to the
good/bad mix). The logging (default) policy is the source of the ``chosen`` argmax, so it
ALWAYS re-picks its own choice -> ``Q(default) ~ 0`` by construction; any positive
``Q(learned)`` is pure signal.

SECONDARY — replay/direct off-policy value: ``V(w) = mean outcome over decisions where
argmax score(w) == chosen`` (the only decisions with an observed counterfactual). Reported
for context.

On a pure-noise source the honest verdict is ``stay_shadow`` — which is the point: keep the
learned weights shadow-only until enough real candidate-log + feedback pairs validate them.

Run ``python -m app.ml.q8_scoring.evaluate`` for the current (synthetic-fixture) verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.logic.constraint_engine.candidate import DEFAULT_SCORE_WEIGHTS, validate_score_weights
from app.ml.q8_scoring.build_training_frame import (
    AXES,
    CHOSEN_COLUMN,
    GROUP_COLUMN,
    LABEL_COLUMN,
    OUTCOME_COLUMN,
    build_pairwise_frame,
    decision_split,
)
from app.ml.q8_scoring.train import learn_profile

# Promotion thresholds — conservative; a learned re-ranking must clearly and safely help.
MIN_Q_IMPROVEMENT = 0.10   # Q(learned) - Q(default), in top-1-agreement units
MIN_LEARNED_Q = 0.10       # learned must clear an absolute floor, not just beat ~0
MIN_TEST_DECISIONS = 30    # coverage: too few real (pool, outcome) pairs -> untrustworthy


@dataclass
class EvalReport:
    n_test_decisions: int
    n_good_decisions: int
    n_bad_decisions: int
    q_default: float
    q_learned: float
    q_improvement: float          # q_learned - q_default (positive = learned ranks better)
    replay_value_default: float
    replay_value_learned: float
    weight_violations: list[str]
    verdict: str                  # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision_top1_and_outcome(
    tidy: pd.DataFrame, weights: dict[str, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per decision: (w picks the chosen candidate?, decision is good?, chosen outcome).

    A decision contributes only if it has exactly one logged ``chosen`` candidate.
    """
    w = np.array([weights[a] for a in AXES], dtype=float)
    picks_chosen: list[bool] = []
    is_good: list[bool] = []
    chosen_outcome: list[float] = []
    for _, grp in tidy.groupby(GROUP_COLUMN, sort=False):
        chosen_mask = grp[CHOSEN_COLUMN].astype(bool)
        if int(chosen_mask.sum()) != 1:
            continue
        feats = grp[list(AXES)].to_numpy(dtype=float)
        scores = feats @ w
        top_idx = int(np.argmax(scores))
        chosen_pos = int(np.flatnonzero(chosen_mask.to_numpy())[0])
        picks_chosen.append(top_idx == chosen_pos)
        is_good.append(bool(grp[LABEL_COLUMN].iloc[0]))
        chosen_outcome.append(float(grp[OUTCOME_COLUMN].iloc[0]))
    return np.array(picks_chosen), np.array(is_good), np.array(chosen_outcome)


def _q_and_replay(
    tidy: pd.DataFrame, weights: dict[str, float]
) -> tuple[float, float]:
    """Return ``(Q, replay_value)`` for a weight vector on a tidy frame."""
    picks, good, outcome = _decision_top1_and_outcome(tidy, weights)
    if picks.size == 0:
        return 0.0, float("nan")
    p_good = float(picks[good].mean()) if good.any() else 0.0
    p_bad = float(picks[~good].mean()) if (~good).any() else 0.0
    q = p_good - p_bad
    matched = picks  # w agrees with the logged choice -> counterfactual outcome observed
    replay = float(outcome[matched].mean()) if matched.any() else float("nan")
    return q, replay


def evaluate(rows_or_tidy: Any, *, holdout_frac: float = 0.30) -> EvalReport:
    """Fit weights on held-in decisions, then gate on the held-out decisions.

    Accepts either raw DB-/fixture-shaped rows (``list[dict]``) or an already-built tidy
    frame. Learns on the train split, scores learned-vs-default ranking on the test split.
    """
    from app.ml.q8_scoring.build_training_frame import build_tidy_frame

    tidy = rows_or_tidy if isinstance(rows_or_tidy, pd.DataFrame) else build_tidy_frame(rows_or_tidy)
    train_df, test_df = decision_split(tidy, holdout_frac=holdout_frac)

    profile = learn_profile(build_pairwise_frame(train_df))
    learned = profile.weights

    q_default, replay_default = _q_and_replay(test_df, dict(DEFAULT_SCORE_WEIGHTS))
    q_learned, replay_learned = _q_and_replay(test_df, learned)
    improvement = q_learned - q_default

    _, good, _ = _decision_top1_and_outcome(test_df, dict(DEFAULT_SCORE_WEIGHTS))
    n_test = int(good.size)
    n_good = int(good.sum())
    n_bad = n_test - n_good
    violations = validate_score_weights(learned)

    reasons: list[str] = []
    if n_test < MIN_TEST_DECISIONS:
        reasons.append(f"only {n_test} test decisions < {MIN_TEST_DECISIONS} (insufficient coverage)")
    if n_good == 0 or n_bad == 0:
        reasons.append("test split lacks both good and bad decisions (Q undefined)")
    if improvement < MIN_Q_IMPROVEMENT:
        reasons.append(f"Q improvement {improvement:.3f} < {MIN_Q_IMPROVEMENT}")
    if q_learned < MIN_LEARNED_Q:
        reasons.append(f"learned Q {q_learned:.3f} < absolute floor {MIN_LEARNED_Q}")
    if violations:
        reasons.append(f"learned weights violate guardrails: {violations}")

    return EvalReport(
        n_test_decisions=n_test,
        n_good_decisions=n_good,
        n_bad_decisions=n_bad,
        q_default=round(q_default, 4),
        q_learned=round(q_learned, 4),
        q_improvement=round(improvement, 4),
        replay_value_default=round(replay_default, 4),
        replay_value_learned=round(replay_learned, 4),
        weight_violations=violations,
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def main() -> None:
    from app.ml.q8_scoring.build_training_frame import synthetic_decision_rows

    planted = evaluate(synthetic_decision_rows(planted=True, seed=0))
    noise = evaluate(synthetic_decision_rows(planted=False, seed=0))
    print("PLANTED (state_fit matters):")
    print(json.dumps(planted.as_dict(), indent=2))
    print(f"\nVERDICT (planted): {planted.verdict}")
    print("\nNOISE (no signal):")
    print(json.dumps(noise.as_dict(), indent=2))
    print(f"\nVERDICT (noise): {noise.verdict}")


if __name__ == "__main__":
    main()
