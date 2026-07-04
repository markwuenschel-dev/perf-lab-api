"""Prescriber block session preferences (Phase 3a of the goal-anchored program plan).

A training block can carry a target session length + accessory-work
preferences (`target_session_minutes`, `accessory_emphasis`, `accessory_focus`
on MesocycleBlock). The prescriber honors them on top of the Phase 0
`exercise_slots` mechanism: it appends accessory slots (by emphasis + focus /
weak-point tags) after the winning template's primary slots, and nudges
`rx.duration_min` toward an explicit target.

Non-DB: calls `recommend_next_session` directly with a `block_context` dict,
mirroring the Phase 0 tests in test_prescriber_exercise_selection.py.
"""

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.planning import periodization_envelope
from app.logic.prescriber import recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

# The winning "Powerlifting" candidate under a neutral state (no kpi_summary) is
# "SBD Strength" (branch pl_sbd_main): duration_min=80, 4 exercise_slots
# (Back Squat, Bench Press, Deadlift, Back-off Squat). See
# app/logic/candidate_library.py POWERLIFTING_TEMPLATES.
_PRIMARY_NAMES = ["Back Squat", "Bench Press", "Deadlift", "Back-off Squat"]
_TEMPLATE_DURATION = 80


def _neutral_state(*, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState(muscular=muscular)
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


def test_high_emphasis_appends_accessories():
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"accessory_emphasis": "high", "accessory_focus": ["posterior_chain"]},
    )
    names = [e.name for e in rx.exercises]
    assert names[: len(_PRIMARY_NAMES)] == _PRIMARY_NAMES
    assert len(names) > len(_PRIMARY_NAMES)
    assert any(n in names for n in ("Romanian Deadlift", "Back Extension"))


def test_minimal_emphasis_appends_none():
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"accessory_emphasis": "minimal", "accessory_focus": ["posterior_chain"]},
    )
    assert [e.name for e in rx.exercises] == _PRIMARY_NAMES


def test_target_minutes_sets_duration():
    rx45 = recommend_next_session(
        _neutral_state(), goal="Powerlifting",
        block_context={"target_session_minutes": 45},
    )
    rx90 = recommend_next_session(
        _neutral_state(), goal="Powerlifting",
        block_context={"target_session_minutes": 90},
    )
    assert 30 <= rx45.duration_min <= 120
    assert 30 <= rx90.duration_min <= 120
    assert rx45.duration_min < rx90.duration_min
    assert rx45.duration_min == 45
    assert rx90.duration_min == 90


def test_short_target_caps_accessories():
    """Short target below the template's own duration_min (80) caps appended
    accessories at 1, even under "high" emphasis."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={
            "accessory_emphasis": "high",
            "accessory_focus": ["posterior_chain"],
            "target_session_minutes": 40,
        },
    )
    names = [e.name for e in rx.exercises]
    assert len(names) == len(_PRIMARY_NAMES) + 1
    assert rx.duration_min == 40


def test_focus_falls_back_to_weak_points():
    """No accessory_focus, but an active weak-point tag present → accessories
    bias toward the weak-point tag."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        active_weak_points=["posterior_chain"],
        block_context={"accessory_emphasis": "balanced"},
    )
    names = [e.name for e in rx.exercises]
    assert "Romanian Deadlift" in names
    assert "Back Extension" in names
    assert len(names) == len(_PRIMARY_NAMES) + 2


def test_focus_without_emphasis_defaults_to_balanced():
    """accessory_focus set but accessory_emphasis omitted → missing/None emphasis
    is treated as "balanced" (design decision), appending up to 2 accessories."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"accessory_focus": ["posterior_chain"]},
    )
    names = [e.name for e in rx.exercises]
    assert len(names) == len(_PRIMARY_NAMES) + 2
    assert "Romanian Deadlift" in names
    assert "Back Extension" in names


def test_target_only_appends_no_accessories():
    """target_session_minutes and accessory_emphasis are independent prefs:
    setting only a session length must drive duration but NOT inject any
    accessories (emphasis/focus both unset)."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"target_session_minutes": 90},
    )
    assert rx.duration_min == 90
    assert [e.name for e in rx.exercises] == _PRIMARY_NAMES
    assert rx.why is not None
    assert not any("block:accessories=" in c for c in rx.why.constraints_applied)


def test_block_context_without_new_keys_is_unchanged():
    """Regression: a block_context WITHOUT any of the new keys (the shape of
    every block created before this migration) behaves exactly as before —
    no accessories appended, duration scaled only by periodization."""
    rx = recommend_next_session(
        _neutral_state(),
        goal="Powerlifting",
        block_context={"week_number": 2, "duration_weeks": 8, "deload_every_n_weeks": 4},
    )
    assert [e.name for e in rx.exercises] == _PRIMARY_NAMES
    expected_vol = periodization_envelope(8, 2, 4).volume_modifier
    assert rx.duration_min == round(_TEMPLATE_DURATION * expected_vol)
