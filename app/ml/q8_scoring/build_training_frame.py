"""Build the Q8 scoring-weight training frame (offline policy evaluation, shadow-only).

Turns the logged candidate pools into a supervised, leakage-safe frame for LEARNING TO
RANK the prescription score-weight vector over the eight axes on ``SessionCandidate``.

Each prescription decision logged its full ranked candidate pool (the per-candidate score
components), which candidate was ``chosen`` by the current policy, and — joined through the
planned session — the athlete's feedback outcome (satisfaction / adherence / pain). We want
a weight vector that ranks GOOD-outcome candidates above the chosen one better than
``DEFAULT_SCORE_WEIGHTS``.

TWO frames come out of here:

* the *tidy* frame — one row per (decision, candidate) with the eight axis features, the
  ``chosen`` flag and the decision's ``good_outcome`` label. Used by the ranking evaluator.
* the *pairwise* frame — for each decision one row per (chosen, rejected) pair, feature =
  ``components(chosen) - components(rejected)`` and ``pair_label = 1`` iff the chosen led to
  a GOOD outcome (so the chosen SHOULD outrank the rejected) else ``0`` (the rejected should
  have won). A no-intercept logistic on this is a Bradley-Terry / RankNet model whose
  coefficient vector IS a score-weight vector.

LEAKAGE (see also ``scoring_weight_features.FORBIDDEN_FEATURE_COLUMNS``):
  * ``final_score`` is NEVER a feature — it is the current policy's composite of the very
    weights we are relearning; using it would just re-derive the defaults.
  * the ``chosen`` flag is NEVER a per-candidate feature — it defines the ranking pairs /
    which candidate's outcome we observed (label structure), not a model input.
  * the outcome columns (``status``/``satisfaction``/``pain``/``followed``) are the LABEL.
  * ``hard_failed`` candidates bypass scoring and are dropped from the ranked pool.

Real candidate-log + feedback pairs are only just being captured, so the pipeline runs and
is tested on a synthetic fixture (:func:`synthetic_decision_rows`) with a planted
"state_fit matters" signal — the DB-backed source is ``scoring_weight_features.build_dataset``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.analysis.feature_builders.scoring_weight_features import (
    FORBIDDEN_FEATURE_COLUMNS,
    SCORE_AXES,
    normalize_candidate_row,
    outcome_score,
)

# Re-exported so the trainer/evaluator/tests share one axis ordering + leakage manifest.
AXES: tuple[str, ...] = SCORE_AXES
FORBIDDEN_FEATURES: dict[str, str] = FORBIDDEN_FEATURE_COLUMNS

GROUP_COLUMN = "decision_id"
CHOSEN_COLUMN = "chosen"
LABEL_COLUMN = "good_outcome"
OUTCOME_COLUMN = "outcome_score"
PAIR_LABEL = "pair_label"


def build_tidy_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize DB-/fixture-shaped candidate rows into the tidy per-candidate frame.

    ``hard_failed`` candidates are dropped (they bypass scoring). Returns one row per
    surviving (decision, candidate) with columns ``decision_id``, the eight ``AXES``,
    ``chosen`` and the decision's ``good_outcome`` / ``outcome_score`` label.
    """
    norm = [normalize_candidate_row(r) for r in rows]
    frame = pd.DataFrame(norm)
    if frame.empty:
        cols = [GROUP_COLUMN, *AXES, CHOSEN_COLUMN, "hard_failed", OUTCOME_COLUMN, LABEL_COLUMN]
        return pd.DataFrame(columns=cols)
    frame = frame[~frame["hard_failed"].astype(bool)].reset_index(drop=True)
    for axis in AXES:
        frame[axis] = frame[axis].astype(float)
    return frame[[GROUP_COLUMN, *AXES, CHOSEN_COLUMN, OUTCOME_COLUMN, LABEL_COLUMN]]


def build_pairwise_frame(tidy: pd.DataFrame) -> pd.DataFrame:
    """Emit the (chosen - rejected) difference rows for pairwise logistic learning-to-rank.

    For each decision with exactly one ``chosen`` candidate, at least one rejected
    candidate and an observed outcome, emit one row per rejected candidate:
    feature = ``components(chosen) - components(rejected)`` over ``AXES`` and
    ``pair_label = 1`` iff the chosen led to a GOOD outcome else ``0``.
    """
    diffs: list[dict[str, Any]] = []
    for decision_id, grp in tidy.groupby(GROUP_COLUMN, sort=False):
        chosen_mask = grp[CHOSEN_COLUMN].astype(bool)
        if int(chosen_mask.sum()) != 1:
            continue  # ambiguous / missing logged choice -> skip
        rejected = grp[~chosen_mask]
        if rejected.empty:
            continue  # singleton pool -> no ranking information
        chosen = grp[chosen_mask].iloc[0]
        label = 1 if bool(chosen[LABEL_COLUMN]) else 0
        chosen_vec = chosen[list(AXES)].to_numpy(dtype=float)
        for _, rej in rejected.iterrows():
            diff = chosen_vec - rej[list(AXES)].to_numpy(dtype=float)
            row: dict[str, Any] = {GROUP_COLUMN: decision_id, PAIR_LABEL: label}
            row.update({axis: float(diff[i]) for i, axis in enumerate(AXES)})
            diffs.append(row)
    if not diffs:
        return pd.DataFrame(columns=[GROUP_COLUMN, *AXES, PAIR_LABEL])
    return pd.DataFrame(diffs)[[GROUP_COLUMN, *AXES, PAIR_LABEL]]


def decision_split(
    tidy: pd.DataFrame, holdout_frac: float = 0.30
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole DECISIONS (grouped) so no decision straddles train/test.

    A decision is the ranking group; splitting by candidate would leak a group's outcome
    across the split. Returns ``(train_tidy, test_tidy)``.
    """
    ids = np.sort(tidy[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = tidy[GROUP_COLUMN].isin(test_ids)
    train_df = tidy[~is_test].reset_index(drop=True)
    test_df = tidy[is_test].reset_index(drop=True)
    return train_df, test_df


# ---------------------------------------------------------------------------
# Synthetic fixture — keeps the pipeline runnable/testable before real logs exist.
# ---------------------------------------------------------------------------
def synthetic_decision_rows(
    n_decisions: int = 240,
    *,
    planted: bool = True,
    pool_size: int = 5,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Generate DB-shaped candidate rows with a planted (or noise) outcome signal.

    Each decision draws ``pool_size`` candidates with uniform [0, 1] axis values; the
    ``chosen`` candidate is the argmax under ``DEFAULT_SCORE_WEIGHTS`` (mirroring the
    logging policy = current default). The outcome is:

    * ``planted=True``  — GOOD with probability ``sigmoid(6 * (chosen.state_fit - 0.5))``,
      so a state_fit-heavy weight vector separates good from bad decisions and beats the
      default (which, being the logging policy, cannot re-rank its own choices).
    * ``planted=False`` — GOOD with probability 0.5, independent of the axes (pure noise).

    GOOD -> satisfaction 5 / completed / followed; BAD -> satisfaction 2 / skipped.
    """
    from app.logic.constraint_engine.candidate import DEFAULT_SCORE_WEIGHTS

    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for decision_id in range(n_decisions):
        pool = [{axis: float(rng.random()) for axis in AXES} for _ in range(pool_size)]
        scores = [sum(DEFAULT_SCORE_WEIGHTS[a] * c[a] for a in AXES) for c in pool]
        chosen_idx = int(np.argmax(scores))
        if planted:
            p_good = 1.0 / (1.0 + np.exp(-6.0 * (pool[chosen_idx]["state_fit"] - 0.5)))
        else:
            p_good = 0.5
        good = bool(rng.random() < p_good)
        if good:
            fb = {"status": "completed", "satisfaction_score": 5, "followed_as_prescribed": True}
        else:
            fb = {"status": "skipped", "satisfaction_score": 2, "followed_as_prescribed": False}
        for i, cand in enumerate(pool):
            rows.append(
                {
                    "decision_id": decision_id,
                    **cand,
                    "chosen": i == chosen_idx,
                    "hard_failed": False,
                    **fb,
                }
            )
    return rows


def synthetic_frame(
    n_decisions: int = 240, *, planted: bool = True, seed: int = 0
) -> pd.DataFrame:
    """Convenience: synthetic rows -> tidy frame."""
    return build_tidy_frame(synthetic_decision_rows(n_decisions, planted=planted, seed=seed))


# Silence "imported but unused" for the re-exported leakage manifest / label helper that
# exist so downstream code and tests import them from one place.
_ = (FORBIDDEN_FEATURES, outcome_score)
