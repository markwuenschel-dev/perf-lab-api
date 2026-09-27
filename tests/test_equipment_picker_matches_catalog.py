"""The web equipment picker can express every tag the exercise catalog requires.

``equipment_available`` (app/logic/exercise_slot.py) keeps an exercise only when every tag in
its ``equipment_required`` is in the athlete's configured set. The web app is the only place
an athlete configures that set, and it can only offer ``EQUIPMENT_TAGS``
(web/src/perflab/equipment.ts). A catalog tag missing from the picker therefore makes every
exercise that needs it unreachable for anyone who configures equipment in the app: SkiErg,
sandbag and wall-ball stations made both HYROX halves and Running + Functional ineligible.

This test lives on the Python side because only here can both lists be read: the catalog is
Python data (seed_exercises.EXERCISES + exercise_bulk.bulk_exercises), and the picker is a
literal array in a TypeScript source file. The backend stores ``equipment`` as a free list
(app/schemas/profile.py), so the picker is the only vocabulary there is.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.data.exercise_bulk import bulk_exercises
from app.logic.exercise_slot import _ALWAYS_AVAILABLE  # pyright: ignore[reportPrivateUsage]
from app.scripts.seed_exercises import EXERCISES

EQUIPMENT_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "perflab" / "equipment.ts"


def _picker_tags() -> list[str]:
    text = EQUIPMENT_TS.read_text(encoding="utf-8")
    block = re.search(r"export const EQUIPMENT_TAGS[^=]*=\s*\[(.*?)\];", text, re.S)
    assert block is not None, "EQUIPMENT_TAGS array not found in equipment.ts"
    return re.findall(r'tag:\s*"([^"]+)"', block.group(1))


def _catalog_tags() -> set[str]:
    rows = [*EXERCISES, *bulk_exercises()]
    tags = {
        t.strip().lower()
        for r in rows
        for t in (r.get("equipment_required") or [])
        if t and t.strip()
    }
    return tags - _ALWAYS_AVAILABLE


def test_the_picker_parses() -> None:
    tags = _picker_tags()
    assert len(tags) >= 13
    assert len(tags) == len(set(tags)), "a tag is offered twice"


def test_every_catalog_equipment_tag_can_be_selected_in_the_app() -> None:
    missing = sorted(_catalog_tags() - set(_picker_tags()))
    assert not missing, (
        f"the catalog requires {missing}, which the web picker cannot offer; every exercise "
        "needing them is unreachable for an athlete who configures equipment"
    )


def test_the_picker_offers_nothing_the_catalog_never_requires() -> None:
    dead = sorted(set(_picker_tags()) - _catalog_tags())
    assert not dead, f"picker tags no exercise requires: {dead}"
