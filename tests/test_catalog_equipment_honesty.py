"""A catalog row may not be "bodyweight by omission" (ADR-0072).

An exercise with no required equipment is offered to a bodyweight-only athlete. Six rows
listed none although they cannot be done without an implement: Chest-to-Bar Pull-Up, and five
strongman lifts and carries. The guards below stop the two classes that produced them. The
correction reaches environments where those rows already exist through the seeder's
idempotent ``equipment_corrections`` step, because the seed itself is insert-only.
"""

from __future__ import annotations

from typing import Any

from app.data.exercise_bulk import bulk_exercises
from app.scripts.seed_exercises import EQUIPMENT_CORRECTED, EXERCISES, equipment_corrections

ROWS: list[dict[str, Any]] = list(EXERCISES) + list(bulk_exercises())
BY_NAME = {r["name"]: r for r in ROWS}


def _equipment(row: dict[str, Any]) -> set[str]:
    return {e for e in (row.get("equipment_required") or []) if e not in ("", "none", "bodyweight")}


def test_a_strongman_implement_is_declared() -> None:
    missing = [r["name"] for r in ROWS if "strongman" in (r.get("sport_domains") or [])
               and not _equipment(r)]
    assert missing == []


def test_a_vertical_pull_needs_something_to_pull_on() -> None:
    missing = [r["name"] for r in ROWS if r["movement_pattern"] == "pull_vertical"
               and not _equipment(r)]
    assert missing == []


def test_the_corrected_rows_now_declare_their_implement() -> None:
    expected = {
        "Chest-to-Bar Pull-Up": {"pullup_bar"},
        "Atlas Stone Load": {"atlas_stone"},
        "Log Clean and Press": {"log_bar"},
        "Yoke Walk": {"yoke"},
        "Tire Flip": {"tire"},
        "Keg Carry": {"keg"},
    }
    assert set(EQUIPMENT_CORRECTED) == set(expected)
    for name, tags in expected.items():
        assert _equipment(BY_NAME[name]) == tags, name


def test_an_existing_row_with_the_old_requirement_is_corrected() -> None:
    fixes = equipment_corrections(ROWS, {"Keg Carry": [], "Chest-to-Bar Pull-Up": None})
    assert fixes == {"Keg Carry": ["keg"], "Chest-to-Bar Pull-Up": ["pullup_bar"]}


def test_the_correction_is_idempotent_and_touches_only_listed_rows() -> None:
    assert equipment_corrections(ROWS, {"Keg Carry": ["keg"]}) == {}
    # Back Squat is not listed: whatever is stored, the correction step leaves it alone.
    assert equipment_corrections(ROWS, {"Back Squat": []}) == {}
