"""A planned running day prescribes running (phase 5).

The defect this closes: the threshold templates declared no exercise slots, so the prescriber
fell back to the generic equipment map and a Running/Threshold day prescribed Air Squat,
Push-up, Lunges. These tests go through the real prescriber (`recommend_next_session`) with
the real seeded catalog, for every running slot the planner can put on a calendar.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.exercise_slot import CatalogExercise
from app.logic.prescriber import recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _healthy() -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState()
    t = TissueState()
    return UnifiedStateVector(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **sync_legacy_from_vectors(cx, f, t),
    )


def _run_names(catalog: list[CatalogExercise]) -> set[str]:
    return {c.name for c in catalog if c.movement_pattern == "run"}


@pytest.mark.parametrize(
    ("goal", "kpi", "branch_id"),
    [
        ("HalfMarathon", {}, "run_threshold"),                      # continuous tempo
        ("5K", {"run_fatigue_factor": 20.0}, "run_threshold_ff"),   # intervals
    ],
    ids=["tempo", "intervals"],
)
def test_a_threshold_day_prescribes_running(
    catalog_snapshot: list[CatalogExercise], goal: str, kpi: dict[str, float], branch_id: str
) -> None:
    scored: list[SessionCandidate] = []
    rx = recommend_next_session(
        _healthy(),
        goal=goal,  # type: ignore[arg-type]
        kpi_summary=kpi,
        catalog=catalog_snapshot,
        block_context={"session_domain": "running", "session_category": "Threshold Work"},
        candidate_log_out=scored,
    )

    # The prescription carries no branch id (and its focus is rewritten from the exercises);
    # the scored pool's head is the template that won.
    assert scored[0].branch_id == branch_id, [c.branch_id for c in scored]
    names = [e.name for e in rx.exercises]
    assert names, "a threshold day prescribed nothing"
    assert set(names) <= _run_names(catalog_snapshot), names
