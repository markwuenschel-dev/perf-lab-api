"""The four planned days that prescribed generic filler now prescribe their own work (phase 9.1).

Before phase 9 their templates had no exercise slots, so a planned Strength — Volume,
Accessory / Isolation or Neural Priming day prescribed "Air Squat, Push-up, Lunges"
(``equipment:fallback_bodyweight``). The content is each template's own ``focus`` text,
encoded as slots. Numbers the text did not state are authored and marked in the template.
"""

from __future__ import annotations

import pytest

from app.logic.prescriber import recommend_next_session
from app.scripts import simulate_matrix as sm


@pytest.fixture(scope="module")
def catalog():
    return sm._catalog()


def _prescribe(catalog, goal: str, domain: str, category: str, equipment=None):
    level_key, _ = sm.EXPERIENCE["intermediate"]
    return recommend_next_session(
        sm._state(level_key, *sm.FRESHNESS["fresh"]),
        goal=goal,  # type: ignore[arg-type]
        catalog=catalog,
        available_equipment=equipment,
        block_context={
            "block_goal": goal, "session_category": category, "session_domain": domain,
            "week_number": 2, "duration_weeks": 8, "deload_every_n_weeks": 4,
        },
    )


@pytest.mark.parametrize(
    ("goal", "domain", "category", "branch", "movements"),
    [
        ("Strength", "strength", "Strength — Volume", "strength_volume",
         {"Front Squat", "Barbell Row"}),
        ("Hypertrophy", "hypertrophy", "Accessory / Isolation", "hyp_maintenance",
         {"Pec Deck", "Leg Curl"}),
        ("Power", "power", "Neural Priming", "power_neural_prime", {"Med Ball Chest Pass"}),
    ],
)
@pytest.mark.parametrize("equipment", [None, "full_gym"])
def test_a_bound_day_prescribes_its_own_authored_movements(
    catalog, goal, domain, category, branch, movements, equipment
) -> None:
    kit = (
        sorted({e for ex in catalog for e in ex.equipment_required if e})
        if equipment == "full_gym" else None
    )
    rx = _prescribe(catalog, goal, domain, category, kit)
    assert rx.why is not None
    codes = rx.why.constraints_applied
    assert rx.why.prescription_branch == branch
    assert f"plan:session_followed={branch}" in codes
    assert "equipment:fallback_bodyweight" not in codes
    names = {e.name for e in rx.exercises}
    assert movements <= names, names
    assert not {"Air Squat", "Push-up", "Lunges"} & names


def test_neural_priming_jumps_and_throws_at_rpe_six(catalog) -> None:
    rx = _prescribe(catalog, "Power", "power", "Neural Priming")
    assert len(rx.exercises) == 2
    by_name = {e.name: e for e in rx.exercises}
    assert "Med Ball Chest Pass" in by_name
    assert all(e.rpe_cap == 6.0 for e in rx.exercises)


def test_strength_volume_front_squat_is_four_by_six_capped_at_seven(catalog) -> None:
    rx = _prescribe(catalog, "Strength", "strength", "Strength — Volume")
    fs = next(e for e in rx.exercises if e.name == "Front Squat")
    assert (fs.sets, fs.reps, fs.rpe_cap) == (4, "6", 7.0)
    row = next(e for e in rx.exercises if e.name == "Barbell Row")
    assert (row.sets, row.reps) == (3, "10")


def test_accessory_focus_is_replaced_for_a_high_habit_athlete_and_says_so(catalog) -> None:
    """strength_variety is gated on habit_strength < 0.45 (an adherence template). For an
    established athlete the day is replaced in-domain and the code says so. Recorded in phase
    9.1, not changed."""
    rx = _prescribe(catalog, "Strength", "strength", "Accessory Focus")
    assert rx.why is not None
    assert rx.why.prescription_branch == "strength_max"
    assert "plan:session_replaced=strength_accessory(unavailable)" in rx.why.constraints_applied


def test_accessory_focus_prescribes_its_variety_session_when_eligible(catalog) -> None:
    from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY
    from app.logic.exercise_slot import resolve_slots

    variety = next(t for t in GOAL_TEMPLATE_LIBRARY["strength"] if t.branch_id == "strength_variety")
    names = {r.chosen.name for r in resolve_slots(list(variety.exercise_slots), catalog) if r.chosen}
    assert names == {"Box Squat", "Trap Bar Deadlift", "Med Ball Slam"}
