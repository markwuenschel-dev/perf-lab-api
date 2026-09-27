"""Readiness redirects prescribe what they say, under every kit (ADR-0072).

A readiness redirect is not a library template: it is built from fatigue signals in
``prescriber._readiness_redirect`` and carries no exercise slots. Before this fix, a
redirect that won the day fell to the generic equipment map. So "Active Recovery" for an
athlete with a barbell prescribed Back Squat / Romanian Deadlift / Bench Press, and every
redirect for a bodyweight athlete prescribed Air Squat / Push-up / Lunges.

Now, with a catalog loaded:
* ``readiness_peripheral_neural_priming`` ("Jumps / Throws (Low Volume, Long Rest)") takes
  the AUTHORED content with the identical intent: ``power_neural_prime``, or its jumps-only
  kit variant when the throw's med ball is not available;
* the other redirects have no authored content with their intent, so they are
  EXERCISE-FREE. Their focus ("Walking / Light Sled Drag", "Zone 2 Cardio (Bike / Row)",
  "Movement Drills <50% Intensity") is the instruction, as the safety overrides already are.
  Nothing is invented and nothing generic is listed.

The generic equipment map stays only for a caller with no catalog loaded.
"""

from __future__ import annotations

import pytest

from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.prescriber import (
    _readiness_redirect,  # pyright: ignore[reportPrivateUsage]
    _redirect_or_template_slots,  # pyright: ignore[reportPrivateUsage]
    _select_exercises,  # pyright: ignore[reportPrivateUsage]
    recommend_next_session,
)
from app.scripts import simulate_matrix as sm
from app.scripts.kit_matrix import kit_equipment

#: Everything the generic equipment map can list (prescriber._EQUIPMENT_EXERCISE_MAP).
MAP_NAMES = {
    "Back Squat", "Romanian Deadlift", "Bench Press", "Air Squat", "Push-up", "Lunges",
    "Pull-up", "Dumbbell Bench Press", "Goblet Squat", "Kettlebell Swing",
}
KITS = ("unconfigured", "full_gym", "home", "bodyweight", "barbell")
EXERCISE_FREE = {
    "readiness_cns_aerobic_shift",
    "readiness_cns_technique",
    "readiness_peripheral_active_recovery",
}


@pytest.fixture(scope="module")
def catalog():
    return sm._catalog()


def _kit(name: str, catalog) -> list[str] | None:
    return ["barbell"] if name == "barbell" else kit_equipment(name, catalog)  # type: ignore[arg-type]


def _state(**legacy: float):
    level, _ = sm.EXPERIENCE["intermediate"]
    s = sm._state(level, *sm.FRESHNESS["fresh"]).model_copy(deep=True)
    for k, v in legacy.items():
        setattr(s, k, v)
    return s


def _redirects() -> list[tuple[SessionCandidate, str]]:
    """Every redirect the prescriber can emit, with the goal that produced it."""
    out: list[tuple[SessionCandidate, str]] = []
    for goal, state in [
        ("Strength", _state(f_nm_central=70.0)),
        ("Strength", _state(f_nm_central=70.0, c_met_aerobic=0.0)),
        ("Power", _state(f_nm_peripheral=70.0)),
        ("Strength", _state(f_nm_peripheral=70.0)),
    ]:
        out += [(c, goal) for c in _readiness_redirect(state, goal, {})]  # type: ignore[arg-type]
    return out


def test_every_redirect_branch_is_covered() -> None:
    assert {c.branch_id for c, _ in _redirects()} == EXERCISE_FREE | {
        "readiness_peripheral_neural_priming"
    }


@pytest.mark.parametrize("kit", KITS)
def test_a_redirect_never_lists_generic_equipment_map_work(catalog, kit) -> None:
    eq = _kit(kit, catalog)
    for candidate, _goal in _redirects():
        slots = _redirect_or_template_slots(candidate, catalog, eq)
        selection = _select_exercises(slots, eq, catalog)
        names = {e.name for e in selection.exercises}
        assert not names & MAP_NAMES, (candidate.branch_id, kit, names)
        assert "equipment:fallback_bodyweight" not in selection.equipment_codes
        if candidate.branch_id in EXERCISE_FREE:
            assert selection.exercises == [], (candidate.branch_id, kit)


@pytest.mark.parametrize("kit", KITS)
def test_the_neural_priming_redirect_prescribes_neural_priming(catalog, kit) -> None:
    eq = _kit(kit, catalog)
    candidate = next(c for c, _ in _redirects()
                     if c.branch_id == "readiness_peripheral_neural_priming")
    selection = _select_exercises(_redirect_or_template_slots(candidate, catalog, eq), eq, catalog)
    assert selection.exercises, kit
    assert all(e.rpe_cap == 6.0 for e in selection.exercises)
    patterns = {c.name: c.movement_pattern for c in catalog}
    assert all(patterns[e.name] == "jump" or e.name == "Med Ball Chest Pass"
               for e in selection.exercises), [e.name for e in selection.exercises]


def test_active_recovery_with_a_barbell_is_not_a_barbell_session(catalog) -> None:
    """The reported case, end to end: a fatigued hypertrophy athlete with only a barbell. No
    hypertrophy session is whole with a barbell alone, so the Active Recovery redirect wins.
    It must not prescribe Back Squat / Romanian Deadlift / Bench Press."""
    rx = recommend_next_session(_state(f_nm_peripheral=70.0), goal="Hypertrophy",
                                catalog=catalog, available_equipment=["barbell"])
    assert rx.why is not None
    assert rx.why.prescription_branch == "readiness_peripheral_active_recovery"
    assert rx.exercises == []
    assert rx.type == "Active Recovery"
    assert rx.focus == "Walking / Light Sled Drag"


@pytest.mark.parametrize(
    ("goal", "legacy", "branch"),
    [
        ("Strength", {"f_nm_central": 70.0}, "readiness_cns_aerobic_shift"),
        ("Strength", {"f_nm_central": 70.0, "c_met_aerobic": 0.0}, "readiness_cns_technique"),
        ("Strength", {"f_nm_peripheral": 70.0}, "readiness_peripheral_active_recovery"),
    ],
)
def test_a_bodyweight_redirect_day_lists_no_filler(catalog, goal, legacy, branch) -> None:
    rx = recommend_next_session(_state(**legacy), goal=goal,  # type: ignore[arg-type]
                                catalog=catalog, available_equipment=["bodyweight"])
    assert rx.why is not None and rx.why.prescription_branch == branch
    assert rx.exercises == []
    assert not any(c == "equipment:fallback_bodyweight" for c in rx.why.constraints_applied)


def test_the_equipment_map_remains_only_without_a_catalog() -> None:
    selection = _select_exercises([], ["barbell"], None)
    assert [e.name for e in selection.exercises][:1] == ["Back Squat"]
