"""Prescriber exercise selection (Phase 0 of the goal-anchored program plan).

Bug: recommend_next_session correctly picks a goal-specific CandidateTemplate,
but finalization overwrote rx.exercises from an equipment-only map, so a
Powerlifting athlete with no equipment configured got the bodyweight default
(Tempo Back Squat / Push-up / Split Squat) instead of SBD work. Templates now carry
structured exercise_slots that finalization prefers over the equipment map;
empty slots must still fall back to the equipment map exactly as before.
"""

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.exercise_slot import ExerciseSlot
from app.logic.prescriber import (
    _exercise_list_for_candidate,
    _exercise_list_for_equipment,
    recommend_next_session,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _neutral_state(*, muscular: float = 0.0) -> UnifiedStateVector:
    """Mid readiness state — no safety override / readiness redirect fires.

    ``muscular`` lets a caller raise muscular fatigue (not a safety trigger) to
    steer the Hypertrophy pool toward the slot-less `hyp_maintenance` template.
    """
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


def test_powerlifting_prescription_returns_sbd_not_bodyweight(catalog_snapshot):
    rx = recommend_next_session(
        _neutral_state(), goal="Powerlifting", available_equipment=None,
        catalog=catalog_snapshot,
    )
    names = [e.name for e in rx.exercises]
    joined = " ".join(names)
    assert any(m in joined for m in ("Squat", "Bench", "Deadlift")), names
    assert "Push-up" not in names
    assert "Air Squat" not in names
    assert "Lunges" not in names


def test_empty_slots_falls_back_to_equipment_map(catalog_snapshot):
    """A template with empty exercise_slots must preserve today's exact
    equipment-fallback behavior (the guard added in Task 0.B)."""
    fallback = _exercise_list_for_equipment(None)
    guarded = _exercise_list_for_candidate([], None)
    assert [e.model_dump() for e in guarded] == [e.model_dump() for e in fallback]

    fallback_barbell = _exercise_list_for_equipment(["barbell"])
    guarded_barbell = _exercise_list_for_candidate([], ["barbell"])
    assert [e.model_dump() for e in guarded_barbell] == [e.model_dump() for e in fallback_barbell]


def test_resolved_slots_take_precedence_over_equipment_map(catalog_snapshot):
    """A template with slots resolves them against the catalog and never touches the fallback.

    Both slots pin by benchmark code, so this also pins that a pinned competition lift is
    returned exactly — not a metadata-equivalent squat or press.
    """
    slots = [
        ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat"),
        ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_bench"),
    ]

    guarded = _exercise_list_for_candidate(slots, ["barbell"], catalog_snapshot)

    assert [e.name for e in guarded] == ["Back Squat", "Bench Press"]


def test_slots_fall_back_to_the_equipment_map_without_a_catalog(catalog_snapshot):
    """No catalog (a pure-logic caller, or an unseeded database) must still yield a session.

    Degrading to the equipment map is the honest behaviour — returning nothing would hand the
    athlete an empty workout.
    """
    slots = [ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat")]

    out = _exercise_list_for_candidate(slots, ["barbell"], None)

    assert out, "a missing catalog must not produce an empty session"


def test_empty_slot_template_reaches_equipment_map_end_to_end(catalog_snapshot):
    """Full-stack guard: a real winning template with EMPTY exercise_slots must
    reach the equipment map through the whole recommend_next_session pipeline
    (scoring -> sort -> finalize -> exercise selection), not just via the
    _exercise_list_for_candidate unit above.

    We steer the Hypertrophy pool to `hyp_maintenance` (its slot-less template)
    with elevated muscular fatigue. If a future dev populates hyp_maintenance's
    exercise_slots, this test goes red — that is intentional: pick a different
    slot-less template rather than deleting the coverage.
    """
    s = _neutral_state(muscular=70.0)
    rx = recommend_next_session(
        s, goal="Hypertrophy", available_equipment=["barbell", "pullup_bar"],
        catalog=catalog_snapshot,
    )
    assert rx.type == "Maintenance Volume"  # hyp_maintenance won (slot-less)
    names = [e.name for e in rx.exercises]
    # Names come from _EQUIPMENT_EXERCISE_MAP["barbell"] + ["pullup_bar"], not slots.
    assert "Back Squat" in names
    assert "Pull-up" in names


# ---------------------------------------------------------------------------
# The session title names what was resolved (S-A)
# ---------------------------------------------------------------------------

def _expected_focus(names: list[str]) -> str:
    head = " + ".join(names[:3])
    return f"{head} + {len(names) - 3} more" if len(names) > 3 else head


def test_focus_names_the_resolved_primary_exercises(catalog_snapshot):
    """Template titles name exercises ("Leg Press 4×12 + Hack Squat 3×15 …") while slots resolve by
    pattern, so the title could name movements the session did not contain. It is rebuilt from
    the resolved list."""
    rx = recommend_next_session(
        _neutral_state(), goal="Hypertrophy", available_equipment=None, catalog=catalog_snapshot,
    )
    names = [e.name for e in rx.exercises]
    assert names
    assert rx.focus == _expected_focus(names)
    assert "Hack Squat" not in rx.focus  # not in the catalog; only ever in template prose


def test_focus_excludes_appended_accessories(catalog_snapshot):
    rx = recommend_next_session(
        _neutral_state(), goal="Hypertrophy", catalog=catalog_snapshot,
        active_weak_points=["posterior_chain"],
        block_context={"accessory_emphasis": "balanced"},
    )
    accessories = [e.name for e in rx.exercises if e.load_note == "Accessory — autoregulate by RPE"]
    primaries = [e.name for e in rx.exercises if e.load_note != "Accessory — autoregulate by RPE"]
    assert accessories, "the block asked for accessories"
    assert rx.focus == _expected_focus(primaries)


def test_focus_is_kept_when_the_session_has_no_exercises(catalog_snapshot):
    """A safety override prescribes no exercise list; the title finalization gave it stands.

    (High lumbar stress also fails the universal tissue rule, so finalization replaces the safety
    candidate with its constraint override — that title, not one rebuilt from exercises.)"""
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState()
    t = TissueState(lumbar=70.0)
    state = UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={"squat": 0.5},
        **sync_legacy_from_vectors(cx, f, t),
    )
    rx = recommend_next_session(state, goal="Strength", catalog=catalog_snapshot)
    assert rx.exercises == []
    assert rx.focus == "Easy movement + mobility (constraint override)"


# ---------------------------------------------------------------------------
# Equipment availability holds for appended accessories; preference is measured (S-C)
# ---------------------------------------------------------------------------

ACCESSORY_NOTE = "Accessory — autoregulate by RPE"
ACCESSORY_BLOCK = {"accessory_emphasis": "high", "accessory_focus": ["push", "pull"]}


def _needs(catalog) -> dict[str, set[str]]:
    return {
        ex.name: {e for e in ex.equipment_required if e not in ("bodyweight", "none", "")}
        for ex in catalog
    }


def test_accessories_respect_the_equipment_the_athlete_listed(catalog_snapshot):
    """Accessories come from hard-coded name lists and used to skip the availability check."""
    rx = recommend_next_session(
        _neutral_state(), goal="Hypertrophy", catalog=catalog_snapshot,
        available_equipment=["barbell"], block_context=ACCESSORY_BLOCK,
    )
    needs = _needs(catalog_snapshot)
    accessories = [e.name for e in rx.exercises if e.load_note == ACCESSORY_NOTE]
    assert all(name in needs and needs[name] <= {"barbell"} for name in accessories), accessories
    skipped = [c for c in rx.why.constraints_applied if c.startswith("equipment:accessories_skipped=")]  # type: ignore[union-attr]
    assert len(skipped) == 1 and int(skipped[0].split("=")[1]) > 0


def test_accessories_are_not_filtered_when_equipment_is_not_set(catalog_snapshot):
    """Not set means unknown — accessories behave as before and nothing is reported skipped."""
    rx = recommend_next_session(
        _neutral_state(), goal="Hypertrophy", catalog=catalog_snapshot,
        available_equipment=None, block_context=ACCESSORY_BLOCK,
    )
    accessories = [e.name for e in rx.exercises if e.load_note == ACCESSORY_NOTE]
    assert accessories == ["Dumbbell Shoulder Press", "Dips", "Chest-Supported Row", "Face Pull"]
    assert not any(c.startswith("equipment:accessories_skipped=") for c in rx.why.constraints_applied)  # type: ignore[union-attr]


def test_the_preference_line_reports_the_measured_change(catalog_snapshot):
    """hyp_high_vol's hinge slot resolves to Leg Curl; preferring dumbbells picks a dumbbell hinge."""
    common = {"goal": "Hypertrophy", "catalog": catalog_snapshot, "available_equipment": None}
    plain = recommend_next_session(_neutral_state(), **common)
    preferred = recommend_next_session(_neutral_state(), equipment_preference=["dumbbell"], **common)

    plain_names = [e.name for e in plain.exercises]
    preferred_names = [e.name for e in preferred.exercises]
    differing = sum(1 for a, b in zip(plain_names, preferred_names, strict=True) if a != b)
    codes = [c for c in preferred.why.constraints_applied if c.startswith("equipment:preference=")]  # type: ignore[union-attr]
    assert codes == [f"equipment:preference=dumbbell(changed={differing})"]
    assert differing >= 1
    assert not any(c.startswith("equipment:preference=") for c in plain.why.constraints_applied)  # type: ignore[union-attr]
