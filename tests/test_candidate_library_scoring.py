"""Characterization tests for candidate_library strength scoring.

Locks the exact SessionCandidate output of each strength-domain template so the
migration to data-driven scoring (ScoringSpec on the template) is provably
behavior-preserving. readiness is passed explicitly (0.7) so the golden values
don't depend on overall_readiness().
"""
from datetime import UTC, datetime

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.candidate_library import STRENGTH_TEMPLATES, score_template
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

R = 0.7


def _state() -> UnifiedStateVector:
    # Chosen so no weak points are flagged (max_strength>=40, aerobic>=200,
    # skill>=35, mobility>=35, grip fatigue<=40) → weak_point_coverage == 0.
    cx = CapacityState(aerobic=300.0, max_strength=60.0, hypertrophy=50.0,
                        skill=50.0, mobility=50.0)
    f = FatigueState(cns=20.0, muscular=30.0, grip=10.0)
    # lumbar+knee=20 must differ from hip=25 so the golden values discriminate a
    # tissue-axis swap between strength_max (lumbar+knee) and strength_volume (hip).
    t = TissueState(lumbar=8.0, knee=12.0, hip=25.0)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={}, **leg,
    )


def _by_branch(branch_id: str):
    t = next(t for t in STRENGTH_TEMPLATES if t.branch_id == branch_id)
    return score_template(t, _state(), {}, readiness=R)


# (branch_id, state_fit, fatigue_penalty, tissue_penalty, weak_point_coverage, habit_bonus)
_GOLDEN = [
    ("strength_max",       0.63, 0.20, 0.20, 0.0, 0.5),
    ("strength_skill_acq", 0.90, 0.10, 0.00, 0.0, 0.5),
    ("strength_variety",   0.70, 0.15, 0.04, 0.0, 0.8),
    ("strength_volume",    0.70, 0.21, 0.25, 0.0, 0.25),
]


@pytest.mark.parametrize("branch,state_fit,fat,tis,wpc,habit", _GOLDEN)
def test_strength_scoring_is_preserved(branch, state_fit, fat, tis, wpc, habit):
    c = _by_branch(branch)
    assert c.state_fit == pytest.approx(state_fit)
    assert c.fatigue_penalty == pytest.approx(fat)
    assert c.tissue_penalty == pytest.approx(tis)
    assert c.weak_point_coverage == pytest.approx(wpc)
    assert c.habit_bonus == pytest.approx(habit)
    # passthrough fields intact
    assert c.branch_id == branch
    assert c.goal_alignment == next(t.goal_alignment for t in STRENGTH_TEMPLATES if t.branch_id == branch)
