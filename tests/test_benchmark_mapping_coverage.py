"""
Tests for Phase-1B benchmark observation->state mapping coverage (ADR-0034).

- Every non-deferred benchmark definition has at least one MAPPINGS row.
- Every capacity/residual mapping's definition has usable standardization_rules
  (numeric floor != cap) so the residual anchor can normalize a raw value.
- A good raw value moves the mapped capacity axis up, via
  apply_benchmark_observation + normalize_score01.

Pure unit tests: no DB access, imports the seed data directly.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.state_update_v0 import apply_benchmark_observation, normalize_score01
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.scripts.seed_benchmarks import BENCHMARKS, MAPPINGS

# Deferred by design: grip domain, tissue-vector-only, and validator-only defs.
_DEFERRED_EXTRA_CODES = {"gym_false_grip_hang"}

_BENCH_BY_CODE = {b["code"]: b for b in BENCHMARKS}


def _is_deferred(bench: dict) -> bool:
    if bench.get("is_validator_only"):
        return True
    if bench["domain"] == "grip":
        return True
    if bench["code"] in _DEFERRED_EXTRA_CODES:
        return True
    return False


def _mapped_codes() -> set[str]:
    return {m["benchmark_code"] for m in MAPPINGS}


def test_every_non_deferred_def_has_a_mapping():
    mapped = _mapped_codes()
    missing = [
        b["code"] for b in BENCHMARKS
        if not _is_deferred(b) and b["code"] not in mapped
    ]
    assert not missing, f"Non-deferred benchmark defs missing a MAPPINGS row: {missing}"


def test_every_capacity_residual_mapping_has_usable_standardization_rules():
    bad = []
    for m in MAPPINGS:
        if m["target_vector"] != "capacity" or m["mapping_type"] != "residual":
            continue
        bench = _BENCH_BY_CODE[m["benchmark_code"]]
        rules = bench.get("standardization_rules")
        if not rules:
            bad.append(m["benchmark_code"])
            continue
        floor = rules.get("floor")
        cap = rules.get("cap")
        if floor is None or cap is None or float(floor) == float(cap):
            bad.append(m["benchmark_code"])
    assert not bad, f"Capacity/residual mappings missing usable standardization_rules: {bad}"


def _state(**capacity_overrides: float) -> UnifiedStateVector:
    cx = CapacityState(**capacity_overrides)
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **leg,
    )


def _mapping_namespace(code: str, target_key: str) -> SimpleNamespace:
    m = next(
        row for row in MAPPINGS
        if row["benchmark_code"] == code and row["target_key"] == target_key
    )
    return SimpleNamespace(
        benchmark_definition_id=1,
        target_vector=m["target_vector"],
        target_key=m["target_key"],
        mapping_type=m["mapping_type"],
        coefficient=m["coefficient"],
        intercept=m.get("intercept", 0.0),
        min_value=m.get("min_value"),
        max_value=m.get("max_value"),
        config=m.get("config") or {},
    )


def test_good_sprint_60m_time_raises_power_capacity():
    bench = _BENCH_BY_CODE["sprint_60m_time"]
    mapping = _mapping_namespace("sprint_60m_time", "power")

    baseline_power = 50.0
    s0 = _state(power=baseline_power)

    raw = 6.5  # better_direction="lower"; well inside floor=9.5 .. cap=6.4
    score01 = normalize_score01(bench["better_direction"], raw, bench["standardization_rules"])
    assert score01 is not None

    s1 = apply_benchmark_observation(
        s0,
        raw_value=raw,
        normalized_value=None,
        better_direction=bench["better_direction"],
        observation_weight=bench["observation_weight"],
        mappings=[mapping],
        score01=score01,
    )

    assert s1.capacity_x.power > baseline_power
