"""Equipment-fallback exercises need only the equipment their list is keyed on.

When the winning template declares no exercise slots, the prescriber picks exercises from
`_EQUIPMENT_EXERCISE_MAP` by the athlete's equipment, and uses the "bodyweight" list when
nothing matches — which includes every athlete with no equipment configured. That list used
to prescribe Tempo Back Squat (a barbell lift) and Split Squat (dumbbells), and the
"dumbbells" list prescribed Goblet Squat (a kettlebell lift in the catalog).

Reads the seeder source, so this needs no database.
"""

from app.data.exercise_bulk import bulk_exercises
from app.logic import prescriber
from app.scripts.seed_exercises import EXERCISES

# The equipment owning each fallback key provides. "bodyweight" provides nothing: it is the
# list an athlete with no matching equipment receives.
EQUIPMENT_PROVIDED_BY_KEY: dict[str, set[str]] = {
    "barbell": {"barbell"},
    "dumbbells": {"dumbbells"},
    "pullup_bar": {"pullup_bar"},
    "bodyweight": set(),
}


def _required_equipment() -> dict[str, set[str]]:
    rows = [*EXERCISES, *bulk_exercises()]
    return {row["name"]: set(row.get("equipment_required") or []) for row in rows}


def test_every_fallback_key_declares_what_it_provides() -> None:
    """A new equipment key must say what it provides, or the guard below cannot check it."""
    assert set(prescriber._EQUIPMENT_EXERCISE_MAP) == set(EQUIPMENT_PROVIDED_BY_KEY)


def test_fallback_exercises_need_only_their_key_equipment() -> None:
    required = _required_equipment()
    wrong: dict[str, list[str]] = {}
    for key, items in prescriber._EQUIPMENT_EXERCISE_MAP.items():
        for name, _sets, _reps in items:
            missing = required[name] - EQUIPMENT_PROVIDED_BY_KEY[key]
            if missing:
                wrong[f"{key}: {name}"] = sorted(missing)
    assert not wrong, f"fallback exercises need equipment their list does not provide: {wrong}"


def test_an_athlete_with_no_equipment_gets_only_equipment_free_exercises() -> None:
    required = _required_equipment()
    names = [e.name for e in prescriber._exercise_list_for_equipment(None)]
    assert names, "an athlete with no equipment must still get a session"
    assert [n for n in names if required[n]] == [], names
