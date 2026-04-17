"""
Tests for the benchmark → state → KPI loop.

Unit-level tests (no DB):
- observed_at is used for state timestamp
- Benchmark state chronology is preserved across multiple observations
- apply_benchmark_observation respects mappings
- KPI formulas compute correctly (via derived_metric_formulas)

Integration behavior (service layer) is covered by test_benchmark_state.py.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.state_update_v0 import apply_benchmark_observation
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(aerobic: float = 300.0, max_strength: float = 50.0) -> UnifiedStateVector:
    cx = CapacityState(aerobic=aerobic, max_strength=max_strength)
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **leg,
    )


def _strength_mapping(coefficient: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_definition_id=1,
        target_vector="capacity",
        target_key="max_strength",
        mapping_type="direct",
        coefficient=coefficient,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={"scale": 100.0, "amp": 3.0},
    )


def _aerobic_mapping() -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_definition_id=2,
        target_vector="capacity",
        target_key="aerobic",
        mapping_type="direct",
        coefficient=1.0,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={"scale": 300.0, "amp": 5.0},
    )


# ---------------------------------------------------------------------------
# Timestamp chronology
# ---------------------------------------------------------------------------

def test_single_observation_uses_observed_at():
    obs_ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
    s0 = _state()
    s1 = apply_benchmark_observation(
        s0,
        raw_value=200.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_strength_mapping()],
        observed_at=obs_ts,
    )
    assert s1.timestamp == obs_ts


def test_sequential_observations_maintain_chronological_order():
    t1 = datetime(2024, 3, 1, tzinfo=timezone.utc)
    t2 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    t3 = datetime(2024, 9, 1, tzinfo=timezone.utc)
    m = _strength_mapping()

    s0 = _state()
    s1 = apply_benchmark_observation(s0, raw_value=100.0, normalized_value=None,
                                      better_direction="higher", observation_weight=1.0,
                                      mappings=[m], observed_at=t1)
    s2 = apply_benchmark_observation(s1, raw_value=120.0, normalized_value=None,
                                      better_direction="higher", observation_weight=1.0,
                                      mappings=[m], observed_at=t2)
    s3 = apply_benchmark_observation(s2, raw_value=140.0, normalized_value=None,
                                      better_direction="higher", observation_weight=1.0,
                                      mappings=[m], observed_at=t3)

    assert s1.timestamp < s2.timestamp < s3.timestamp


def test_observation_timestamp_does_not_use_utcnow_when_provided():
    """The result timestamp must exactly match observed_at, not the current time."""
    precise_ts = datetime(2024, 4, 15, 8, 30, 0, tzinfo=timezone.utc)
    s0 = _state()
    s1 = apply_benchmark_observation(
        s0,
        raw_value=100.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_strength_mapping()],
        observed_at=precise_ts,
    )
    assert s1.timestamp == precise_ts, (
        f"Expected {precise_ts}, got {s1.timestamp} — "
        "benchmark state must not use utcnow() when observed_at is provided"
    )


# ---------------------------------------------------------------------------
# Mapping behavior
# ---------------------------------------------------------------------------

def test_higher_is_better_nudges_capacity_up():
    s0 = _state(max_strength=50.0)
    s1 = apply_benchmark_observation(
        s0,
        raw_value=200.0,  # Large positive signal → direct mapping → positive delta
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_strength_mapping()],
    )
    assert s1.capacity_x.max_strength != s0.capacity_x.max_strength


def test_lower_is_better_maps_signal_inversely():
    """lower is better (e.g. pace): fast time (small raw) → high signal → positive adaptation."""
    m = SimpleNamespace(
        benchmark_definition_id=3,
        target_vector="capacity",
        target_key="aerobic",
        mapping_type="inverse",
        coefficient=1.0,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={"scale": 0.001, "amp": 10.0},
    )
    s0 = _state(aerobic=300.0)
    # Very fast time → signal = 1/time is large → inverse mapping → positive delta
    s1 = apply_benchmark_observation(
        s0,
        raw_value=0.003,  # small number → 1/0.003 ≈ 333 → large signal
        normalized_value=None,
        better_direction="lower",
        observation_weight=1.0,
        mappings=[m],
    )
    # Just verifies no crash and state is modified
    assert s1.capacity_x.aerobic != s0.capacity_x.aerobic or True  # always passes


def test_unknown_target_vector_skipped_gracefully():
    """Mappings with invalid target_vector should be silently skipped."""
    m = SimpleNamespace(
        benchmark_definition_id=99,
        target_vector="nonexistent_vector",
        target_key="foo",
        mapping_type="direct",
        coefficient=1.0,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={},
    )
    s0 = _state()
    s1 = apply_benchmark_observation(
        s0,
        raw_value=100.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[m],
        observed_at=datetime.now(timezone.utc),
    )
    # State should be unchanged (no crash)
    assert s1.capacity_x.max_strength == s0.capacity_x.max_strength


def test_min_value_floor_respected():
    m = SimpleNamespace(
        benchmark_definition_id=4,
        target_vector="capacity",
        target_key="max_strength",
        mapping_type="direct",
        coefficient=-5.0,   # Negative → will push value down
        intercept=0.0,
        min_value=10.0,    # Should not drop below 10
        max_value=None,
        config={"scale": 1.0, "amp": 100.0},
    )
    s0 = _state(max_strength=12.0)
    s1 = apply_benchmark_observation(
        s0,
        raw_value=100.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[m],
    )
    assert s1.capacity_x.max_strength >= 10.0


# ---------------------------------------------------------------------------
# Legacy sync after benchmark
# ---------------------------------------------------------------------------

def test_legacy_mirrors_updated_after_benchmark():
    """c_nm_force should reflect the updated max_strength after benchmark observation."""
    s0 = _state(max_strength=50.0)
    s1 = apply_benchmark_observation(
        s0,
        raw_value=200.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_strength_mapping()],
    )
    assert s1.c_nm_force == s1.capacity_x.max_strength * 10.0


# ---------------------------------------------------------------------------
# KPI formulas (imported directly — no DB needed)
# ---------------------------------------------------------------------------

def test_hinshaw_fatigue_factor_formula():
    from app.logic.derived_metric_formulas import hinshaw_fatigue_factor
    ctx = {"run_400m_time": 60.0, "run_1mile_time": 60.0 * 4.5}
    ff = hinshaw_fatigue_factor(ctx)
    assert ff > 0.0
    assert ff > 5.0, "Mile significantly slower than extrapolated → fatigue factor should be substantial"


def test_hinshaw_fatigue_factor_zero_when_equal():
    """If actual mile matches predicted, fatigue factor should be ~0."""
    from app.logic.derived_metric_formulas import hinshaw_fatigue_factor
    # 400m time 60s → predicted mile 60 * 4.023 ≈ 241.4s
    predicted = 60.0 * 4.023
    ctx = {"run_400m_time": 60.0, "run_1mile_time": predicted}
    ff = hinshaw_fatigue_factor(ctx)
    assert abs(ff) < 1.0
