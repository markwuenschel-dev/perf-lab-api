"""Benchmark observation assimilation + derived KPI helpers."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.derived_metric_formulas import hinshaw_fatigue_factor
from app.logic.state_update_v0 import apply_benchmark_observation
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    cx = CapacityState(max_strength=80.0)
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(timezone.utc),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **leg,
    )


def test_hinshaw_fatigue_factor_positive_when_mile_slower_than_extrapolated():
    # 60s 400m -> predicted mile ~60 * 4.023; if mile is much slower, factor positive
    ctx = {"run_400m_time": 60.0, "run_1mile_time": 60.0 * 4.5}
    ff = hinshaw_fatigue_factor(ctx)
    assert ff > 5.0


def test_apply_benchmark_observation_respects_mapping():
    m = SimpleNamespace(
        benchmark_definition_id=1,
        target_vector="capacity",
        target_key="max_strength",
        mapping_type="direct",
        coefficient=0.5,
        intercept=0.0,
        min_value=None,
        max_value=None,
        config={"scale": 100.0, "amp": 4.0},
    )
    s0 = _state()
    before = s0.capacity_x.max_strength
    out = apply_benchmark_observation(
        s0,
        raw_value=200.0,
        normalized_value=None,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[m],
    )
    assert out.capacity_x.max_strength != before
