"""Slot resolution picks a movement from the catalog, and pins the ones the sport pins.

This is the layer that makes exercise selection data-driven (ADR-0016). Four properties matter
enough to fail a build over:

1. A competition lift must NOT be substituted. `squat + barbell` also describes a box squat and
   a pin squat; swapping one into a powerlifter's session is wrong in a way the athlete pays
   for on the platform, so those slots pin by benchmark code.
2. An athlete's equipment must actually filter — that is the whole point of
   `Exercise.equipment_required`, which selection never read before.
3. Unconfigured equipment must NOT be read as "owns nothing". The old fallback did exactly
   that, and a powerlifter with no equipment configured got a bodyweight session.
4. Resolution must be deterministic, or every prescriber test becomes flaky.
"""
from __future__ import annotations

from app.logic.exercise_slot import (
    CatalogExercise,
    ExerciseSlot,
    preferred_load_types,
    resolve_slot,
    resolve_slots,
)


def _ex(name: str, **kw) -> CatalogExercise:
    base: dict[str, object] = {
        "modality": "Strength",
        "movement_pattern": "squat",
        "load_type": "barbell",
        "pattern_family": "squat_family",
        "equipment_required": ("barbell",),
    }
    base.update(kw)
    return CatalogExercise(name=name, **base)  # type: ignore[arg-type]


CATALOG = [
    _ex("Back Squat", e1rm_benchmark_code="pl_e1rm_squat", skill_demand=0.7,
        weak_point_tags=("squat_pattern", "anterior_chain")),
    _ex("Box Squat", skill_demand=0.5),
    _ex("Pin Squat", skill_demand=0.6),
    _ex("Goblet Squat", load_type="kettlebell", equipment_required=("kettlebell",),
        skill_demand=0.2, weak_point_tags=("squat_pattern",)),
    _ex("Air Squat", load_type="bodyweight", equipment_required=(), skill_demand=0.1),
    _ex("Romanian Deadlift", movement_pattern="hinge", pattern_family="hinge_family",
        modality="Hypertrophy", skill_demand=0.5, weak_point_tags=("posterior_chain",)),
    _ex("Good Morning", movement_pattern="hinge", pattern_family="hinge_family",
        skill_demand=0.6, weak_point_tags=("posterior_chain",)),
]


def test_a_pinned_slot_never_substitutes_a_metadata_twin() -> None:
    """The reason pinning exists. Box Squat matches every soft requirement and must still lose."""
    slot = ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat")

    res = resolve_slot(slot, CATALOG)

    assert res.chosen is not None
    assert res.chosen.name == "Back Squat"
    # Box Squat / Pin Squat are squat+barbell+squat_family and lower skill, so an
    # unpinned rank would have preferred one of them. Prove that.
    unpinned = resolve_slot(ExerciseSlot(sets="4", reps="3-5", movement_pattern="squat"), CATALOG)
    assert unpinned.chosen is not None and unpinned.chosen.name != "Back Squat"


def test_equipment_actually_filters() -> None:
    """`Exercise.equipment_required` was never read by selection before this."""
    slot = ExerciseSlot(sets="3", reps="10", movement_pattern="squat")

    res = resolve_slot(slot, CATALOG, available_equipment=frozenset({"kettlebell"}))

    assert res.chosen is not None
    # Only Goblet Squat (kettlebell) and Air Squat (bodyweight) are affordable.
    assert res.chosen.name in {"Goblet Squat", "Air Squat"}
    assert res.chosen.name != "Back Squat", "barbell work must not survive an equipment filter"


def test_bodyweight_movements_need_no_equipment() -> None:
    slot = ExerciseSlot(sets="3", reps="15", movement_pattern="squat")

    res = resolve_slot(slot, CATALOG, available_equipment=frozenset())

    assert res.chosen is not None and res.chosen.name == "Air Squat"


def test_unconfigured_equipment_is_unknown_not_empty() -> None:
    """The bug the old fallback carried: a powerlifter with no equipment set got bodyweight.

    ``None`` means the athlete never told us. That must not narrow the catalog — it is the
    missing-becomes-a-value defect wearing an equipment hat.
    """
    slot = ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat")

    unknown = resolve_slot(slot, CATALOG, available_equipment=None)
    configured_empty = resolve_slot(slot, CATALOG, available_equipment=frozenset())

    assert unknown.chosen is not None and unknown.chosen.name == "Back Squat"
    assert configured_empty.chosen is None, "a configured-empty athlete genuinely has no barbell"
    assert configured_empty.unmet_reason is not None


def test_weak_points_bias_the_choice() -> None:
    """`Exercise.weak_point_tags` was display-only before; now it steers selection."""
    slot = ExerciseSlot(sets="3", reps="8", movement_pattern="hinge")

    neutral = resolve_slot(slot, CATALOG)
    biased = resolve_slot(slot, CATALOG, weak_point_tags=frozenset({"posterior_chain"}))

    assert neutral.chosen is not None and biased.chosen is not None
    # Both hinge movements carry posterior_chain, so the bias cannot change the winner here —
    # what it must not do is break determinism or drop the slot.
    assert biased.chosen.name == neutral.chosen.name


def test_weak_point_bias_outranks_the_simpler_movement() -> None:
    """A flagged deficit should beat the default 'prefer lower skill' tiebreak."""
    catalog = [
        _ex("Simple Thing", movement_pattern="carry", skill_demand=0.1),
        _ex("Targeted Thing", movement_pattern="carry", skill_demand=0.9,
            weak_point_tags=("grip_endurance",)),
    ]
    slot = ExerciseSlot(sets="3", reps="40m", movement_pattern="carry")

    assert resolve_slot(slot, catalog).chosen.name == "Simple Thing"  # type: ignore[union-attr]
    biased = resolve_slot(slot, catalog, weak_point_tags=frozenset({"grip_endurance"}))
    assert biased.chosen is not None and biased.chosen.name == "Targeted Thing"


def test_skill_ceiling_keeps_technical_lifts_out() -> None:
    slot = ExerciseSlot(sets="3", reps="10", movement_pattern="squat", max_skill_demand=0.3)

    res = resolve_slot(slot, CATALOG)

    assert res.chosen is not None and res.chosen.skill_demand <= 0.3


def test_resolution_is_deterministic() -> None:
    """Same inputs, same session — otherwise every prescriber assertion becomes flaky."""
    slot = ExerciseSlot(sets="3", reps="10", movement_pattern="squat")
    picks = {resolve_slot(slot, list(CATALOG)).chosen.name for _ in range(8)}  # type: ignore[union-attr]
    assert len(picks) == 1


def test_later_slots_avoid_earlier_picks() -> None:
    slots = [
        ExerciseSlot(sets="3", reps="8", movement_pattern="hinge"),
        ExerciseSlot(sets="3", reps="10", movement_pattern="hinge"),
    ]

    chosen = [r.chosen.name for r in resolve_slots(slots, CATALOG)]  # type: ignore[union-attr]

    assert len(set(chosen)) == 2, "a session should not silently repeat a movement"


def test_a_back_off_slot_may_repeat_the_main_lift() -> None:
    """Powerlifting needs the same lift twice — top set then back-offs. That is not a bug."""
    slots = [
        ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat"),
        ExerciseSlot(sets="3", reps="6-8", e1rm_code="pl_e1rm_squat", allow_repeat=True),
    ]

    chosen = [r.chosen.name for r in resolve_slots(slots, CATALOG)]  # type: ignore[union-attr]

    assert chosen == ["Back Squat", "Back Squat"]


def test_an_unfillable_slot_reports_why() -> None:
    """One impossible slot costs that movement and says so — it does not fail the session."""
    slots = [
        ExerciseSlot(sets="3", reps="5", movement_pattern="squat"),
        ExerciseSlot(sets="3", reps="5", movement_pattern="handstand_walk"),
    ]

    results = resolve_slots(slots, CATALOG)

    assert results[0].chosen is not None
    assert results[1].chosen is None
    assert "handstand_walk" in (results[1].unmet_reason or "")


# ---------------------------------------------------------------------------
# Equipment preference (S-C): a tie-break that can neither exclude nor admit
# ---------------------------------------------------------------------------

PREFERENCE_CATALOG = [
    _ex("Barbell Row", movement_pattern="pull_horizontal", pattern_family="row", skill_demand=0.3),
    _ex("Dumbbell Row", movement_pattern="pull_horizontal", pattern_family="row", load_type="dumbbell",
        equipment_required=("dumbbells",), skill_demand=0.4),
    _ex("Cable Row", movement_pattern="pull_horizontal", pattern_family="row", load_type="cable",
        equipment_required=("cable",), skill_demand=0.5),
    _ex("Targeted Row", movement_pattern="pull_horizontal", pattern_family="row", skill_demand=0.9,
        weak_point_tags=("posterior_chain",)),
]
ROW = ExerciseSlot(sets="3", reps="10", movement_pattern="pull_horizontal")


def test_a_preference_breaks_the_tie_and_records_that_it_did() -> None:
    plain = resolve_slot(ROW, PREFERENCE_CATALOG)
    preferred = resolve_slot(ROW, PREFERENCE_CATALOG, preferred_load_types=preferred_load_types(["dumbbell"]))

    assert plain.chosen is not None and plain.chosen.name == "Barbell Row"  # simplest
    assert preferred.chosen is not None and preferred.chosen.name == "Dumbbell Row"
    assert preferred.preference_changed is True
    assert plain.preference_changed is False


def test_machines_include_cables() -> None:
    res = resolve_slot(ROW, PREFERENCE_CATALOG, preferred_load_types=preferred_load_types(["machine"]))
    assert res.chosen is not None and res.chosen.name == "Cable Row"


def test_a_preference_never_admits_an_exercise_the_athlete_cannot_do() -> None:
    """Preferring dumbbells with only a barbell must still yield barbell work — not a dumbbell row."""
    res = resolve_slot(
        ROW,
        PREFERENCE_CATALOG,
        available_equipment=frozenset({"barbell"}),
        preferred_load_types=preferred_load_types(["dumbbell"]),
    )
    assert res.chosen is not None and res.chosen.load_type == "barbell"
    assert res.preference_changed is False


def test_a_preference_never_excludes_the_only_option() -> None:
    res = resolve_slot(
        ROW,
        [e for e in PREFERENCE_CATALOG if e.load_type == "barbell"],
        preferred_load_types=preferred_load_types(["machine"]),
    )
    assert res.chosen is not None and res.preference_changed is False


def test_a_weak_point_still_outranks_a_preference() -> None:
    res = resolve_slot(
        ROW,
        PREFERENCE_CATALOG,
        weak_point_tags=frozenset({"posterior_chain"}),
        preferred_load_types=preferred_load_types(["dumbbell"]),
    )
    assert res.chosen is not None and res.chosen.name == "Targeted Row"


def test_a_pinned_lift_ignores_the_preference() -> None:
    slot = ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat")
    res = resolve_slot(slot, CATALOG, preferred_load_types=preferred_load_types(["dumbbell", "machine"]))
    assert res.chosen is not None and res.chosen.name == "Back Squat"


def test_unknown_preference_values_prefer_nothing() -> None:
    assert preferred_load_types(["kettlebell", ""]) == frozenset()
    assert preferred_load_types(None) == frozenset()
