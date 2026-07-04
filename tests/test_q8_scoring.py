"""Pure (non-DB) tests for the Q8 scoring-weight offline policy-eval pipeline.

Covers: (a) the tidy/pairwise frames carry only leakage-safe features (never final_score
or the chosen flag as an input) and the correct pairwise ranking label; (b) train()
learns a weight vector that ROUND-TRIPS through validate_score_weights / ScoreWeightProfile
and recovers the planted state_fit signal, emitting a shadow_only artifact; (c) the
promotion gate PROMOTES on a planted signal and STAYS SHADOW on pure noise. Requires
pandas / scikit-learn (dev extra).
"""
from __future__ import annotations

from typing import Any

from app.analysis.feature_builders.scoring_weight_features import (
    is_good_outcome,
    normalize_candidate_row,
    outcome_score,
    parse_score_components,
)
from app.logic.constraint_engine.candidate import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeightProfile,
    validate_score_weights,
)
from app.ml.q8_scoring.build_training_frame import (
    AXES,
    FORBIDDEN_FEATURES,
    PAIR_LABEL,
    build_pairwise_frame,
    build_tidy_frame,
    synthetic_decision_rows,
)
from app.ml.q8_scoring.evaluate import (
    MIN_LEARNED_Q,
    MIN_Q_IMPROVEMENT,
    EvalReport,
    evaluate,
)
from app.ml.q8_scoring.train import (
    build_artifact,
    fit_raw_weights,
    learn_profile,
    train,
)

_GOOD_FB = {"status": "completed", "satisfaction_score": 5, "followed_as_prescribed": True}
_BAD_FB = {"status": "skipped", "satisfaction_score": 2, "followed_as_prescribed": False}


def _cand(chosen: bool, hard_failed: bool = False, **axes: float) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(AXES, 0.0)
    row.update(axes)
    row["chosen"] = chosen
    row["hard_failed"] = hard_failed
    return row


def _decision(decision_id: int, good: bool, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fb = _GOOD_FB if good else _BAD_FB
    return [{"decision_id": decision_id, **c, **fb} for c in candidates]


# --------------------------------------------------------------------------- outcome + leakage
def test_outcome_score_and_good_label() -> None:
    assert outcome_score(5, "completed", followed_as_prescribed=True) >= 0.9
    assert outcome_score(2, "skipped", followed_as_prescribed=False) < 0.6
    assert is_good_outcome(5, "completed", followed_as_prescribed=True)
    assert not is_good_outcome(2, "skipped", followed_as_prescribed=False)
    # pain flag pulls an otherwise-good outcome down.
    assert outcome_score(5, "completed", followed_as_prescribed=True, pain_flag=True) < outcome_score(
        5, "completed", followed_as_prescribed=True
    )


def test_parse_score_components_json_and_defaults() -> None:
    comps = parse_score_components('{"state_fit": 0.7, "goal_alignment": 0.4, "junk": 9}')
    assert comps["state_fit"] == 0.7
    assert comps["goal_alignment"] == 0.4
    assert set(comps) == set(AXES)  # only the known axes, missing -> 0.0
    assert comps["novelty_bonus"] == 0.0


def test_forbidden_columns_are_never_features() -> None:
    # The leakage manifest and the feature axes are disjoint.
    assert set(FORBIDDEN_FEATURES).isdisjoint(set(AXES))
    for banned in ("final_score", "chosen", "hard_failed", "status", "satisfaction_score"):
        assert banned in FORBIDDEN_FEATURES

    # A row carrying final_score never surfaces it into the normalized axes.
    row = normalize_candidate_row(
        {"decision_id": 1, "final_score": 0.99, "chosen": True, "state_fit": 0.5, **_GOOD_FB}
    )
    assert "final_score" not in row
    assert row["state_fit"] == 0.5


def test_tidy_frame_drops_hard_failed_and_omits_final_score() -> None:
    rows = _decision(
        1,
        good=True,
        candidates=[
            _cand(chosen=True, state_fit=0.9),
            _cand(chosen=False, state_fit=0.2),
            _cand(chosen=False, hard_failed=True, state_fit=0.99),
        ],
    )
    # Even if a stray final_score is logged, it must not become a column.
    for r in rows:
        r["final_score"] = 0.5
    tidy = build_tidy_frame(rows)
    assert len(tidy) == 2  # hard_failed dropped
    assert "final_score" not in tidy.columns
    assert set(AXES) <= set(tidy.columns)


# --------------------------------------------------------------------------- pairwise label
def test_pairwise_label_follows_outcome() -> None:
    good = _decision(1, good=True, candidates=[_cand(True, state_fit=0.9), _cand(False, state_fit=0.1)])
    bad = _decision(2, good=False, candidates=[_cand(True, state_fit=0.2), _cand(False, state_fit=0.8)])
    pw = build_pairwise_frame(build_tidy_frame(good + bad))
    # one pair per (chosen, rejected) per decision -> 2 rows.
    assert len(pw) == 2
    lbl = dict(zip(pw["decision_id"], pw[PAIR_LABEL], strict=True))
    assert lbl[1] == 1  # good outcome -> chosen should outrank rejected
    assert lbl[2] == 0  # bad outcome -> rejected should have won
    # diff feature = components(chosen) - components(rejected).
    good_pair = pw[pw["decision_id"] == 1].iloc[0]
    assert good_pair["state_fit"] == 0.9 - 0.1


# --------------------------------------------------------------------------- training
def test_learned_profile_passes_guardrails_and_is_a_profile() -> None:
    rows = synthetic_decision_rows(planted=True, seed=1)
    profile = learn_profile(build_pairwise_frame(build_tidy_frame(rows)))
    assert isinstance(profile, ScoreWeightProfile)
    assert set(profile.weights) == set(AXES)
    assert validate_score_weights(profile.weights) == []  # round-trips clean


def test_raw_fit_recovers_planted_state_fit_signal() -> None:
    rows = synthetic_decision_rows(planted=True, seed=1)
    raw = fit_raw_weights(build_pairwise_frame(build_tidy_frame(rows)))
    # state_fit is the planted driver -> its coefficient dominates the other axes.
    assert raw["state_fit"] == max(raw.values())
    assert raw["state_fit"] > 0


def test_projection_forces_guardrails_on_adversarial_fit() -> None:
    # Weights that violate every guardrail must be snapped back into the safe box.
    from app.ml.q8_scoring.train import _project_to_guardrails

    bad = {
        "goal_alignment": 0.3,
        "state_fit": 0.3,
        "fatigue_penalty": 0.5,   # wrong sign (should stay negative)
        "tissue_penalty": 0.5,    # wrong sign
        "novelty_bonus": 0.9,     # over cap
        "habit_bonus": -0.4,      # under floor
        "template_bias": 0.1,
        "weak_point_coverage": 0.1,
    }
    proj = _project_to_guardrails(bad)
    assert validate_score_weights(proj) == []


def test_degenerate_labels_fall_back_to_defaults() -> None:
    # All-good decisions -> only one pairwise class -> no ranking signal -> default weights.
    rows = _decision(1, True, [_cand(True, state_fit=0.9), _cand(False, state_fit=0.1)])
    rows += _decision(2, True, [_cand(True, goal_alignment=0.8), _cand(False, goal_alignment=0.2)])
    raw = fit_raw_weights(build_pairwise_frame(build_tidy_frame(rows)))
    assert raw == DEFAULT_SCORE_WEIGHTS


def test_train_emits_shadow_only_artifact() -> None:
    artifact = train(synthetic_decision_rows(planted=True, seed=2))
    assert artifact["version"] == "q8_scoring_weights_v1"
    assert artifact["namespace"] == "q8_scoring"
    assert artifact["shadow_only"] is True
    assert set(artifact["weights"]) == set(AXES)
    assert validate_score_weights(artifact["weights"]) == []
    assert "score_candidate" in artifact["binding"]  # documents the (unwired) binding
    # leakage note + the pairwise label documented in the card-bearing training block.
    assert "final_score" in artifact["training"]["leakage"]


def test_build_artifact_shape() -> None:
    profile = ScoreWeightProfile(weights=dict(DEFAULT_SCORE_WEIGHTS), version="v1-shadow")
    art = build_artifact(profile, n_pairs=10, n_decisions=3)
    assert art["weights"] == dict(DEFAULT_SCORE_WEIGHTS)
    assert art["shadow_only"] is True


# --------------------------------------------------------------------------- promotion gate
def test_gate_promotes_on_planted_signal() -> None:
    report = evaluate(synthetic_decision_rows(planted=True, seed=0))
    assert isinstance(report, EvalReport)
    assert report.q_default == 0.0  # logging policy cannot re-rank its own choices
    assert report.q_improvement >= MIN_Q_IMPROVEMENT
    assert report.q_learned >= MIN_LEARNED_Q
    assert report.weight_violations == []
    assert report.verdict == "promote", report.reasons


def test_gate_stays_shadow_on_noise() -> None:
    report = evaluate(synthetic_decision_rows(planted=False, seed=0))
    assert report.q_improvement < MIN_Q_IMPROVEMENT
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why
    # weights are still guardrail-safe even when the signal is absent.
    assert report.weight_violations == []


def test_gate_report_serializes_with_expected_keys() -> None:
    d = evaluate(synthetic_decision_rows(planted=True, seed=3)).as_dict()
    assert {
        "n_test_decisions", "n_good_decisions", "n_bad_decisions", "q_default", "q_learned",
        "q_improvement", "replay_value_default", "replay_value_learned", "weight_violations",
        "verdict", "reasons",
    } <= set(d)
