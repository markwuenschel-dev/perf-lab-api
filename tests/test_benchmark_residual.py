"""Residual benchmark anchor + backend normalization (ADR-0034).

The capacity anchor moves an axis toward a backend-normalized score by a
confidence-scaled Kalman gain, and shrinks that axis's variance. Below-expectation
results pull the axis *down*; with no score we fall back to the legacy additive nudge.
"""

from types import SimpleNamespace

from app.engine.simulate import baseline_state
from app.logic.state_update_v0 import apply_benchmark_observation, normalize_score01


def _cap_map(key="max_strength", coef=1.0, min_value=None, max_value=None):
    return SimpleNamespace(
        target_vector="capacity", target_key=key, mapping_type="residual",
        coefficient=coef, intercept=0.0, min_value=min_value, max_value=max_value, config={},
    )


def _apply(s, score01, mapping, weight=1.0, better="higher"):
    return apply_benchmark_observation(
        s, raw_value=0.0, normalized_value=None, better_direction=better,
        observation_weight=weight, mappings=[mapping], score01=score01,
    )


def test_above_expectation_raises_capacity():
    s0 = baseline_state(max_strength=50.0)  # expected01 = 0.5
    assert _apply(s0, 0.85, _cap_map()).capacity_x.max_strength > 50.0


def test_below_expectation_lowers_capacity():
    s0 = baseline_state(max_strength=70.0)  # expected01 = 0.7
    assert _apply(s0, 0.3, _cap_map()).capacity_x.max_strength < 70.0


def test_observation_shrinks_confidence():
    s0 = baseline_state(max_strength=50.0)
    v0 = s0.capacity_confidence.max_strength
    assert _apply(s0, 0.85, _cap_map()).capacity_confidence.max_strength < v0


def test_low_confidence_corrects_more():
    s_unsure = baseline_state(max_strength=50.0)  # variance 1.0 (weak prior)
    s_sure = baseline_state(max_strength=50.0)
    s_sure.capacity_confidence.max_strength = 0.1
    a = _apply(s_unsure, 0.9, _cap_map()).capacity_x.max_strength - 50.0
    b = _apply(s_sure, 0.9, _cap_map()).capacity_x.max_strength - 50.0
    assert a > b


def test_aerobic_anchor_respects_ceiling():
    s0 = baseline_state(aerobic=325.0)  # expected01 = 0.5 on ceiling 650
    fast = _apply(s0, 0.9, _cap_map("aerobic"), better="lower")
    slow = _apply(s0, 0.2, _cap_map("aerobic"), better="lower")
    assert fast.capacity_x.aerobic > 325.0
    assert slow.capacity_x.aerobic < 325.0
    assert fast.capacity_x.aerobic <= 650.0


def test_normalize_score01_direction_and_bounds():
    higher = {"floor": 40.0, "cap": 240.0}
    assert normalize_score01("higher", 240.0, higher) == 1.0
    assert normalize_score01("higher", 40.0, higher) == 0.0
    assert 0.45 < normalize_score01("higher", 140.0, higher) < 0.55
    lower = {"floor": 2100.0, "cap": 900.0}
    assert normalize_score01("lower", 900.0, lower) == 1.0
    assert normalize_score01("lower", 2100.0, lower) == 0.0
    assert normalize_score01("higher", 100.0, None) is None


def test_normalized_value_falls_back_to_score():
    s0 = baseline_state(max_strength=50.0)
    s1 = apply_benchmark_observation(
        s0, raw_value=0.0, normalized_value=90.0, better_direction="higher",
        observation_weight=1.0, mappings=[_cap_map()],
    )
    assert s1.capacity_x.max_strength > 50.0


def test_legacy_additive_path_when_no_score():
    """No score01 and no normalized_value → legacy additive nudge (back-compat)."""
    s0 = baseline_state(max_strength=50.0)
    m = SimpleNamespace(
        target_vector="capacity", target_key="max_strength", mapping_type="direct",
        coefficient=0.5, intercept=0.0, min_value=None, max_value=None,
        config={"scale": 100.0, "amp": 4.0},
    )
    s1 = apply_benchmark_observation(
        s0, raw_value=150.0, normalized_value=None, better_direction="higher",
        observation_weight=1.0, mappings=[m],
    )
    assert s1.capacity_x.max_strength != 50.0


def test_seed_residual_mappings_are_normalizable():
    """Every residual seed mapping's benchmark has working standardization_rules."""
    from app.scripts.seed_benchmarks import BENCHMARKS, MAPPINGS

    rules_by_code = {b["code"]: b.get("standardization_rules") for b in BENCHMARKS}
    dir_by_code = {b["code"]: b.get("better_direction") for b in BENCHMARKS}
    for m in MAPPINGS:
        if m["mapping_type"] != "residual":
            continue
        code = m["benchmark_code"]
        rules = rules_by_code.get(code)
        assert rules and "floor" in rules and "cap" in rules, f"{code} missing standardization_rules"
        assert normalize_score01(dir_by_code[code], rules["cap"], rules) == 1.0
        assert normalize_score01(dir_by_code[code], rules["floor"], rules) == 0.0
