"""Prescriber exercise selection (Phase 0 of the goal-anchored program plan).

Bug: recommend_next_session correctly picks a goal-specific CandidateTemplate,
but finalization overwrote rx.exercises from an equipment-only map, so a
Powerlifting athlete with no equipment configured got the bodyweight default
(Tempo Squat / Push-Up / Split Squat) instead of SBD work. Templates now carry
structured exercise_slots that finalization prefers over the equipment map;
empty slots must still fall back to the equipment map exactly as before.
"""

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.prescriber import (
    _exercise_list_for_candidate,
    _exercise_list_for_equipment,
    recommend_next_session,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _neutral_state() -> UnifiedStateVector:
    """Mid readiness state — no safety override / readiness redirect fires."""
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
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
        skill_state={"squat": 0.5},
        **leg,
    )


def test_powerlifting_prescription_returns_sbd_not_bodyweight():
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", available_equipment=None,
    )
    names = [e.name for e in rx.exercises]
    joined = " ".join(names)
    assert any(m in joined for m in ("Squat", "Bench", "Deadlift")), names
    assert "Push-Up" not in names
    assert "Split Squat" not in names
    assert "Tempo Squat" not in names


def test_empty_slots_falls_back_to_equipment_map():
    """A template with empty exercise_slots must preserve today's exact
    equipment-fallback behavior (the guard added in Task 0.B)."""
    fallback = _exercise_list_for_equipment(None)
    guarded = _exercise_list_for_candidate([], None)
    assert [e.model_dump() for e in guarded] == [e.model_dump() for e in fallback]

    fallback_barbell = _exercise_list_for_equipment(["barbell"])
    guarded_barbell = _exercise_list_for_candidate([], ["barbell"])
    assert [e.model_dump() for e in guarded_barbell] == [e.model_dump() for e in fallback_barbell]


def test_nonempty_slots_take_precedence_over_equipment_map():
    slots = [("Back Squat", "4", "3-5"), ("Bench Press", "4", "3-5")]
    guarded = _exercise_list_for_candidate(slots, ["barbell"])
    assert [e.name for e in guarded] == ["Back Squat", "Bench Press"]
