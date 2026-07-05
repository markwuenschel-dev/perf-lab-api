from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.mpc.planner import evaluate_candidates
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _state(fat: float, tis: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState(max_strength=70.0, hypertrophy=55.0)
    f = FatigueState(cns=fat, muscular=fat, metabolic=fat, structural=fat, tendon=fat, grip=fat)
    t = TissueState(knee=tis, lumbar=tis)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=_WHEN, capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


_HARD = SessionCandidate(type="Max Strength", focus="Back Squat 5x3 @ RPE 8.5", rationale="",
                         duration_min=70, branch_id="strength_main", domain="strength", goal_alignment=1.0)
_RECOVERY = SessionCandidate(type="Recovery", focus="Easy mobility + Z2", rationale="",
                             duration_min=30, branch_id="recovery", domain="general", goal_alignment=0.4)


def test_evaluate_is_deterministic():
    a = evaluate_candidates(_state(30.0), [_HARD, _RECOVERY], "Strength", 8.0)
    b = evaluate_candidates(_state(30.0), [_HARD, _RECOVERY], "Strength", 8.0)
    assert [e.candidate.branch_id for e in a] == [e.candidate.branch_id for e in b]
    assert [round(e.breakdown.J, 9) for e in a] == [round(e.breakdown.J, 9) for e in b]


def test_empty_pool_returns_empty():
    assert evaluate_candidates(_state(10.0), [], "Strength", 8.0) == []


def test_fresh_athlete_trains_hard():
    ev = evaluate_candidates(_state(5.0, 5.0), [_HARD, _RECOVERY], "Strength", 8.0)
    assert ev[0].candidate.branch_id == "strength_main"


def test_loaded_athlete_backs_off():
    ev = evaluate_candidates(_state(70.0, 30.0), [_HARD, _RECOVERY], "Strength", 8.0)
    assert ev[0].candidate.branch_id == "recovery"


def test_ranking_is_sorted_by_descending_J():
    ev = evaluate_candidates(_state(45.0, 20.0), [_HARD, _RECOVERY], "Strength", 8.0)
    scores = [e.breakdown.J for e in ev]
    assert scores == sorted(scores, reverse=True)
