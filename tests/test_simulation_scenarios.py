"""
Behavioral simulation scenarios for the state engine.

These drive multi-week synthetic training through the real dose + state-update
pipeline (see app/engine/simulate.py) and assert *trajectory-level* properties.

Two kinds of scenario live here:
  * Invariants that hold on the current engine — they pass today and guard
    against regressions.
  * `xfail(strict=True)` specs for behavior a specific ADR will introduce. When
    that ADR is implemented, drop the marker and the assertion turns green. A
    strict xfail that unexpectedly passes fails the suite, flagging that the
    behavior arrived (or an assumption was wrong).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engine.simulate import (
    aerobic_log,
    apply_benchmark,
    baseline_state,
    capacity_gain,
    capacity_series,
    fatigue_series,
    rest_for,
    run_schedule,
    strength_log,
    weekly_block,
)

# A 3x/week strength microcycle (Mon/Wed/Fri-ish).
_STRENGTH_3X = [(0, strength_log), (2, strength_log), (4, strength_log)]
# Strength on Mon/Wed/Fri, aerobic on Tue/Thu/Sat — concurrent endurance load.
_CONCURRENT = _STRENGTH_3X + [(1, aerobic_log), (3, aerobic_log), (5, aerobic_log)]


def _strength_mapping() -> SimpleNamespace:
    """A higher-is-better benchmark mapping onto max_strength."""
    return SimpleNamespace(
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


# ---------------------------------------------------------------------------
# Invariants — pass on the current engine
# ---------------------------------------------------------------------------

def test_block_runs_and_advances_time():
    s0 = baseline_state(max_strength=50.0)
    steps = weekly_block(_STRENGTH_3X, weeks=4)
    traj = run_schedule(s0, steps)
    assert len(traj) == len(steps) + 1
    # Timestamps strictly increase along the trajectory.
    times = [s.timestamp for s in traj]
    assert times == sorted(times)
    assert times[-1] > times[0]


def test_fatigue_axes_stay_bounded():
    s0 = baseline_state(max_strength=50.0)
    traj = run_schedule(s0, weekly_block(_CONCURRENT, weeks=4))
    for axis in ("cns", "muscular", "metabolic", "structural", "tendon", "grip"):
        series = fatigue_series(traj, axis)
        assert all(0.0 <= v <= 100.0 for v in series), f"{axis} out of [0,100]: {series}"


def test_capacity_nondecreasing_under_training():
    """Today, training never lowers a capacity axis (no decay term yet)."""
    s0 = baseline_state(max_strength=50.0)
    series = capacity_series(run_schedule(s0, weekly_block(_STRENGTH_3X, weeks=4)), "max_strength")
    assert all(b >= a - 1e-9 for a, b in zip(series, series[1:], strict=False)), series


def test_fatigue_decays_during_layoff():
    # Accumulate fatigue, then rest 14 days and confirm CNS fatigue falls.
    s0 = baseline_state(max_strength=50.0)
    trained = run_schedule(s0, weekly_block(_CONCURRENT, weeks=2))[-1]
    rested = rest_for(trained, days=14.0)[-1]
    assert rested.fatigue_f.cns < trained.fatigue_f.cns
    assert rested.fatigue_f.muscular < trained.fatigue_f.muscular


def test_benchmark_moves_target_axis():
    s0 = baseline_state(max_strength=50.0)
    s1 = apply_benchmark(
        s0, raw_value=150.0, mappings=[_strength_mapping()], better_direction="higher"
    )
    assert s1.capacity_x.max_strength != s0.capacity_x.max_strength


# ---------------------------------------------------------------------------
# Future behavior — flip these green as each ADR lands
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="ADR-0033: training must move capacity a visible amount", strict=True)
def test_productive_block_yields_visible_capacity_gain():
    s0 = baseline_state(max_strength=50.0)
    traj = run_schedule(s0, weekly_block(_STRENGTH_3X, weeks=4))
    # A 4-week productive block should build at least a couple of points.
    assert capacity_gain(traj, "max_strength") >= 2.0


@pytest.mark.xfail(reason="ADR-0033: capacities detrain with disuse", strict=True)
def test_detraining_lowers_capacity_after_layoff():
    s0 = baseline_state(max_strength=70.0, aerobic=350.0)
    after = rest_for(s0, days=120.0)[-1]
    assert after.capacity_x.max_strength < s0.capacity_x.max_strength


@pytest.mark.xfail(reason="ADR-0037: concurrent endurance blunts strength adaptation", strict=True)
def test_concurrent_endurance_blunts_strength_gain():
    s0 = baseline_state(max_strength=50.0)
    gain_strength_only = capacity_gain(
        run_schedule(s0, weekly_block(_STRENGTH_3X, weeks=4)), "max_strength"
    )
    gain_concurrent = capacity_gain(
        run_schedule(s0, weekly_block(_CONCURRENT, weeks=4)), "max_strength"
    )
    # Interference: adding heavy endurance should meaningfully cut strength gain.
    assert gain_concurrent < gain_strength_only * 0.8
