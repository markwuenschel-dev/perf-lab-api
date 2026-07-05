from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.mpc.objective import (
    MpcWeights,
    goal_progress,
    score_trajectory,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _state(*, max_strength=70.0, fat=0.0, tis=0.0) -> UnifiedStateVector:
    cx = CapacityState(max_strength=max_strength, hypertrophy=55.0)
    f = FatigueState(cns=fat, muscular=fat, metabolic=fat, structural=fat, tendon=fat, grip=fat)
    tt = TissueState(knee=tis, lumbar=tis)
    leg = sync_legacy_from_vectors(cx, f, tt)
    return UnifiedStateVector(
        timestamp=_WHEN, capacity_x=cx, fatigue_f=f, tissue_t=tt,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _traj(*states) -> list[UnifiedStateVector]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [s.model_copy(update={"timestamp": base + timedelta(days=i)}) for i, s in enumerate(states)]


def test_goal_progress_positive_when_target_axis_grows():
    traj = _traj(_state(max_strength=70.0), _state(max_strength=76.0))
    assert goal_progress(traj, "Strength") > 0.0


def test_goal_progress_zero_for_flat_trajectory():
    traj = _traj(_state(max_strength=70.0), _state(max_strength=70.0))
    assert goal_progress(traj, "Strength") == 0.0


def test_higher_fatigue_trajectory_lowers_J():
    fresh = _traj(_state(fat=5.0), _state(max_strength=72.0, fat=15.0))
    tired = _traj(_state(fat=60.0), _state(max_strength=72.0, fat=70.0))
    j_fresh = score_trajectory(fresh, "Strength", 8.0).J
    j_tired = score_trajectory(tired, "Strength", 8.0).J
    assert j_tired < j_fresh


def test_higher_tissue_trajectory_lowers_J():
    low = _traj(_state(tis=5.0), _state(max_strength=72.0, tis=10.0))
    high = _traj(_state(tis=5.0), _state(max_strength=72.0, tis=75.0))
    assert score_trajectory(high, "Strength", 8.0).J < score_trajectory(low, "Strength", 8.0).J


def test_more_uncertainty_lowers_J():
    traj = _traj(_state(fat=10.0), _state(max_strength=72.0, fat=20.0))
    j_low = score_trajectory(traj, "Strength", belief_trace=2.0).J
    j_high = score_trajectory(traj, "Strength", belief_trace=12.0).J
    assert j_high < j_low


def test_breakdown_terms_sum_to_J():
    traj = _traj(_state(fat=30.0), _state(max_strength=73.0, fat=40.0, tis=20.0))
    b = score_trajectory(traj, "Strength", 8.0)
    assert abs(sum(b.terms.values()) - b.J) < 1e-9


def test_convex_fatigue_penalty_hurts_more_when_already_loaded():
    """Same +10 fatigue costs more from a loaded baseline than a fresh one (convexity)."""
    w = MpcWeights()
    fresh_lo = score_trajectory(_traj(_state(fat=10.0)), "Strength", 0.0, w).fatigue
    fresh_hi = score_trajectory(_traj(_state(fat=20.0)), "Strength", 0.0, w).fatigue
    load_lo = score_trajectory(_traj(_state(fat=60.0)), "Strength", 0.0, w).fatigue
    load_hi = score_trajectory(_traj(_state(fat=70.0)), "Strength", 0.0, w).fatigue
    assert (load_hi - load_lo) > (fresh_hi - fresh_lo)
