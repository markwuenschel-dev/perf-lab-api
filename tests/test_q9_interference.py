"""Pure (non-DB) tests for the Q9 concurrent-training interference ML pipeline.

Covers: (a) build_frame yields the expected columns with no leaked / label-derived
features and a leakage-clean per-athlete efficiency label; (b) the constrained fit RECOVERS
a planted suppression alpha; (c) train() emits a reproducible shadow_only artifact recording
learned-vs-default alphas and the unwired EngineParameters.interference_*_alpha binding;
(d) the promotion gate PROMOTES on a planted suppression signal and STAYS SHADOW on pure
noise (the ADR-0037 interference-floor guardrail vetoes a too-weak learned alpha). Requires
pandas/numpy/scipy (dev extra).
"""
from __future__ import annotations

import numpy as np

from app.engine.parameters import default_parameters
from app.ml.q9_interference.build_training_frame import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    LABEL_COLUMN,
    PAIR_COLUMN,
    build_frame,
    grouped_time_split,
    synthetic_interference_rows,
)
from app.ml.q9_interference.evaluate import (
    MAX_EFFICIENCY_AT_REF,
    MIN_MAE_IMPROVEMENT,
    Z_REF,
    PairEval,
    evaluate,
    evaluate_pair,
)
from app.ml.q9_interference.train import (
    ALPHA_MAX,
    PAIR_TO_AXIS,
    PAIR_TO_PARAM,
    fit_alpha,
    fit_pair,
    pair_defaults,
    suppression_efficiency,
    train,
)

_PLANTED_ALPHA = 1.8


def test_build_frame_columns_and_no_leakage() -> None:
    frame = build_frame(synthetic_interference_rows(n_athletes=8, seed=1))

    for col in (GROUP_COLUMN, "episode", PAIR_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN):
        assert col in frame.columns
    assert len(frame) > 0

    # No forbidden / label-derived signal is exposed as a model feature or carried column.
    leaky = set(FORBIDDEN_FEATURES) - {"label"}
    assert leaky.isdisjoint(set(FEATURE_COLUMNS))
    for bad in ("realized_gain", "post_benchmark", "expected_gain", "better_direction"):
        assert bad not in frame.columns

    # The single predictor is a bounded, non-NaN load fraction; the label is a positive ratio.
    assert not frame["z_interfering_load"].isna().any()
    assert float(frame["z_interfering_load"].min()) >= 0.0
    assert not frame[LABEL_COLUMN].isna().any()
    assert float(frame[LABEL_COLUMN].min()) > 0.0


def test_anchors_normalize_efficiency_near_one() -> None:
    # An athlete's zero-interference anchor blocks should have efficiency ~= 1 (realized ==
    # the athlete's own expected gain), and higher load should give lower efficiency.
    frame = build_frame(synthetic_interference_rows(n_athletes=12, seed=2))
    low = frame[frame["z_interfering_load"] <= 0.1][LABEL_COLUMN].mean()
    high = frame[frame["z_interfering_load"] >= 0.6][LABEL_COLUMN].mean()
    assert 0.9 <= float(low) <= 1.1
    assert float(high) < float(low)  # concurrent load blunts the gain (ADR-0037)


def test_fit_recovers_planted_alpha() -> None:
    frame = build_frame(synthetic_interference_rows(alpha_true=_PLANTED_ALPHA, effect=1.0, seed=3))
    fit = fit_pair(frame, "endurance_on_strength")
    # The constrained NLS fit recovers the planted alpha (default is a distant 3.34).
    assert abs(fit["learned_alpha"] - _PLANTED_ALPHA) < 0.45
    assert fit["default_alpha"] == 3.34
    assert fit["engine_param"] == "interference_e_on_strength_alpha"


def test_fit_alpha_is_bounded_and_regularized() -> None:
    # Pure noise (no z dependence) collapses the fit toward 0, and it never leaves [0, MAX].
    z = np.linspace(0.0, 1.0, 200)
    rng = np.random.default_rng(0)
    flat = np.full_like(z, 1.0) + rng.normal(0.0, 0.05, z.shape)
    a = fit_alpha(z, flat, floor=0.30, default_alpha=3.34)
    assert 0.0 <= a <= ALPHA_MAX
    assert a < 0.3  # no signal -> essentially no suppression


def test_pair_defaults_read_engine_floors() -> None:
    params = default_parameters()
    for pair in PAIR_TO_PARAM:
        field, default_alpha, floor = pair_defaults(pair, params=params)
        assert field == PAIR_TO_PARAM[pair]
        assert default_alpha == float(getattr(params, field))
        expected_floor = params.interference_floor_by_axis.get(PAIR_TO_AXIS[pair], 0.30)
        assert floor == float(expected_floor)


def test_suppression_efficiency_matches_engine_shape() -> None:
    # floor + (1-floor)*exp(-alpha*z): 1.0 at z=0, monotone decreasing toward the floor.
    z = np.array([0.0, 0.5, 1.0, 5.0])
    eff = suppression_efficiency(z, alpha=1.8, floor=0.30)
    assert abs(float(eff[0]) - 1.0) < 1e-9
    assert np.all(np.diff(eff) < 0.0)
    assert float(eff[-1]) >= 0.30


def test_train_emits_reproducible_shadow_only_artifact() -> None:
    frame = build_frame(synthetic_interference_rows(seed=4))
    artifact = train(frame)

    assert artifact["version"] == "q9_interference_priors_v1"
    assert artifact["namespace"] == "q9_interference"
    assert artifact["shadow_only"] is True
    assert artifact["formula"].startswith("gain_efficiency = floor")
    # Records the (unwired) binding to the engine interference alphas.
    assert artifact["target"]["symbol"] == "EngineParameters.interference_*_alpha"
    assert "NOT wired" in artifact["target"]["binding"]

    pair = artifact["pairs"]["endurance_on_strength"]
    assert pair["engine_param"] == "interference_e_on_strength_alpha"
    assert pair["floor"] == 0.30
    assert pair["default_alpha"] == 3.34
    assert 0.0 <= pair["learned_alpha"] <= ALPHA_MAX
    assert pair["alpha_delta"] == round(pair["learned_alpha"] - pair["default_alpha"], 4)


def test_planted_signal_promotes() -> None:
    frame = build_frame(synthetic_interference_rows(alpha_true=_PLANTED_ALPHA, effect=1.0, seed=6))
    result = evaluate_pair(frame, "endurance_on_strength")
    assert isinstance(result, PairEval)
    assert result.mae_improvement > MIN_MAE_IMPROVEMENT
    assert result.suppression_corr < 0.0  # real suppression: efficiency falls with load
    assert result.efficiency_at_ref <= MAX_EFFICIENCY_AT_REF
    assert result.verdict == "promote", result.reasons

    overall = evaluate(frame)
    assert overall["overall_verdict"] == "promote"


def test_pure_noise_stays_shadow_on_floor_guardrail() -> None:
    frame = build_frame(synthetic_interference_rows(effect=0.0, seed=7))
    result = evaluate_pair(frame, "endurance_on_strength")
    assert result.learned_alpha < 0.3          # fit collapses toward no suppression
    assert result.efficiency_at_ref > MAX_EFFICIENCY_AT_REF  # would gut the interference floor
    assert result.verdict == "stay_shadow"
    assert result.reasons  # must explain why
    # The ADR-0037 interference-floor guardrail must be among the veto reasons.
    assert any("floor guardrail" in r for r in result.reasons)

    assert evaluate(frame)["overall_verdict"] == "stay_shadow"


def test_report_serializes_with_expected_keys() -> None:
    frame = build_frame(synthetic_interference_rows(alpha_true=_PLANTED_ALPHA, effect=1.0, seed=8))
    d = evaluate_pair(frame, "endurance_on_strength").as_dict()
    assert {
        "pair", "engine_param", "default_alpha", "learned_alpha", "mae_default",
        "mae_learned", "mae_improvement", "suppression_corr", "efficiency_at_ref",
        "sparse_mae_improvement", "verdict", "reasons",
    } <= set(d)
    assert d["efficiency_at_ref"] == round(
        float(suppression_efficiency(np.array([Z_REF]), d["learned_alpha"], d["floor"])[0]), 4
    )


def test_grouped_split_holds_out_whole_athletes() -> None:
    frame = build_frame(synthetic_interference_rows(seed=9))
    train_df, test_df = grouped_time_split(frame)
    assert set(train_df[GROUP_COLUMN]).isdisjoint(set(test_df[GROUP_COLUMN]))
    assert len(train_df) + len(test_df) == len(frame)


def test_other_pair_floor_and_binding() -> None:
    # A CNS->skill fixture uses the skill floor (0.50) and the cns_on_skill engine alpha.
    params = default_parameters()
    _, default_alpha, floor = pair_defaults("cns_on_skill", params=params)
    assert floor == 0.50
    frame = build_frame(
        synthetic_interference_rows(pair="cns_on_skill", alpha_true=1.4, floor=floor, seed=11)
    )
    result = evaluate_pair(frame, "cns_on_skill")
    assert result.engine_param == "interference_cns_on_skill_alpha"
    assert result.floor == 0.50
    # A genuine planted suppression on a higher-floor axis still recovers a plausible alpha.
    assert 0.0 <= result.learned_alpha <= ALPHA_MAX
