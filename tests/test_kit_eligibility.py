"""Every planned day × equipment kit is honest, or a known gap on its way out (phase 9).

The invariant (ADR-0072): equipment adaptation may change the exercise implementation. It
must not silently change the session's training intent or sport identity. For each planned
day (every ``planned_session_slots`` binding) and each kit in ``app.logic.kit_support``:

* supported kit -> the planned template or a stated in-domain replacement, every authored
  slot realized;
* unsupported kit -> a stated in-domain replacement or explicit ``plan:session_unavailable``;
* never generic filler (the equipment map, a slot-less template, another domain's template);
* never a partial session (some of an authored session's slots).

``KNOWN_GAPS`` is a RATCHET: it must equal today's violations exactly. A fix that makes a
cell honest fails this test until the cell is deleted from the list, and a new violation fails
it too. Phase 9 empties it.

Classification is re-derived from the winning template's slots against the same kit
(``app/scripts/kit_matrix.py``), because the explanation codes cannot tell the equipment map
from a real catalog selection.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.logic.constraint_labels import _PLAN_SLOTS
from app.logic.kit_support import KIT_SUPPORT, KITS
from app.scripts import simulate_matrix as sm
from app.scripts.kit_matrix import KitOutcome, bindings, build_kit_matrix, evaluate

ROOT = Path(__file__).resolve().parents[1]

#: (domain, planned category, kit) -> today's dishonest outcome. Shrinks to empty in phase 9.
KNOWN_GAPS: dict[tuple[str, str, str], str] = {
    ("strength", "Max Strength", "home"): "filler",
    ("strength", "Max Strength", "bodyweight"): "filler",
    ("strength", "Strength — Volume", "unconfigured"): "filler",
    ("strength", "Strength — Volume", "full_gym"): "filler",
    ("strength", "Strength — Volume", "home"): "filler",
    ("strength", "Strength — Volume", "bodyweight"): "filler",
    ("strength", "Accessory Focus", "home"): "filler",
    ("strength", "Accessory Focus", "bodyweight"): "filler",
    ("hypertrophy", "High Volume Upper", "home"): "partial",
    ("hypertrophy", "High Volume Upper", "bodyweight"): "filler",
    ("hypertrophy", "High Volume Lower", "home"): "partial",
    ("hypertrophy", "High Volume Lower", "bodyweight"): "filler",
    ("hypertrophy", "Accessory / Isolation", "unconfigured"): "filler",
    ("hypertrophy", "Accessory / Isolation", "full_gym"): "filler",
    ("hypertrophy", "Accessory / Isolation", "home"): "filler",
    ("hypertrophy", "Accessory / Isolation", "bodyweight"): "filler",
    ("hypertrophy", "High Volume", "home"): "partial",
    ("hypertrophy", "High Volume", "bodyweight"): "filler",
    ("power", "Power Development", "home"): "partial",
    ("power", "Power Development", "bodyweight"): "partial",
    ("power", "Neural Priming", "unconfigured"): "filler",
    ("power", "Neural Priming", "full_gym"): "filler",
    ("power", "Neural Priming", "home"): "filler",
    ("power", "Neural Priming", "bodyweight"): "filler",
    ("power", "Strength Potentiation", "home"): "filler",
    ("power", "Strength Potentiation", "bodyweight"): "filler",
    ("powerlifting", "SBD Strength", "home"): "filler",
    ("powerlifting", "SBD Strength", "bodyweight"): "filler",
    ("powerlifting", "Accessory Focus", "home"): "filler",
    ("powerlifting", "Accessory Focus", "bodyweight"): "filler",
    ("weightlifting", "Weightlifting Technique", "home"): "filler",
    ("weightlifting", "Weightlifting Technique", "bodyweight"): "filler",
    ("mixed", "MetCon", "home"): "partial",
    ("mixed", "MetCon", "bodyweight"): "partial",
    ("mixed", "Metabolic Conditioning", "home"): "partial",
    ("mixed", "Metabolic Conditioning", "bodyweight"): "partial",
    ("mixed", "Mixed Modal", "home"): "partial",
    ("mixed", "Mixed Modal", "bodyweight"): "partial",
    ("mixed", "Engine Work", "home"): "filler",
    ("mixed", "Engine Work", "bodyweight"): "filler",
    ("mixed", "Strength Endurance", "home"): "partial",
    ("mixed", "Strength Endurance", "bodyweight"): "partial",
    ("mixed", "Hyrox Simulation", "home"): "filler",
    ("mixed", "Hyrox Simulation", "bodyweight"): "filler",
    ("mixed", "Running + Functional", "home"): "filler",
    ("mixed", "Running + Functional", "bodyweight"): "filler",
    ("mixed", "Strength + Skill", "home"): "filler",
    ("mixed", "Strength + Skill", "bodyweight"): "filler",
    ("calisthenics", "Gymnastics Conditioning", "bodyweight"): "partial",
    ("grip", "Grip & Support", "bodyweight"): "partial",
    ("general", "Aerobic + Strength", "home"): "filler",
    ("general", "Aerobic + Strength", "bodyweight"): "filler",
    ("general", "Strength Preservation", "home"): "filler",
    ("general", "Strength Preservation", "bodyweight"): "filler",
    ("general", "Metabolic Conditioning", "home"): "partial",
    ("general", "Metabolic Conditioning", "bodyweight"): "partial",
    ("conditioning", "Metabolic Conditioning", "home"): "partial",
    ("conditioning", "Metabolic Conditioning", "bodyweight"): "partial",
}

#: Planned days a full gym supports but an athlete who configured equipment IN THE WEB APP
#: cannot reach, because the picker cannot express a tag the day needs. Known unsupported UI
#: configurations, not passes: separate PR A2 (web equipment tags) empties this.
UI_UNSUPPORTED: dict[tuple[str, str], str] = {
    ("mixed", "Hyrox Simulation"): "filler",
    ("mixed", "Running + Functional"): "filler",
}


@pytest.fixture(scope="module")
def catalog():
    return sm._catalog()


@pytest.fixture(scope="module")
def matrix(catalog) -> list[KitOutcome]:
    return build_kit_matrix(catalog)


def test_every_planned_day_has_an_authored_kit_row() -> None:
    slugs = {b.slug for _, _, b in bindings()}
    assert set(KIT_SUPPORT) == slugs
    for slug, row in KIT_SUPPORT.items():
        assert set(row) == set(KITS), slug


def test_every_cell_is_honest_or_a_known_gap(matrix: list[KitOutcome]) -> None:
    actual = {(r.domain, r.category, r.kit): r.outcome for r in matrix if not r.ok}
    newly_dishonest = {k: v for k, v in actual.items() if k not in KNOWN_GAPS}
    now_honest = sorted(k for k in KNOWN_GAPS if k not in actual)
    changed = {k: (KNOWN_GAPS[k], v) for k, v in actual.items()
               if k in KNOWN_GAPS and KNOWN_GAPS[k] != v}
    assert not newly_dishonest, f"new dishonest cells: {newly_dishonest}"
    assert not now_honest, f"fixed: delete from KNOWN_GAPS: {now_honest}"
    assert not changed, f"outcome changed (was, now): {changed}"


def test_unconfigured_equipment_is_never_unavailable(matrix: list[KitOutcome]) -> None:
    """Never configuring equipment keeps its permissive meaning: nothing is filtered."""
    bad = [(r.domain, r.category) for r in matrix
           if r.kit == "unconfigured" and r.outcome == "unavailable"]
    assert bad == []


def test_a_known_gap_is_never_counted_as_honest(matrix: list[KitOutcome]) -> None:
    """Filler and partial are never acceptable, whatever the expectation says."""
    for r in matrix:
        if r.outcome in ("filler", "partial"):
            assert not r.ok, r


def _web_picker_tags() -> list[str]:
    src = (ROOT / "web/src/perflab/equipment.ts").read_text(encoding="utf-8")
    block = src.split("EQUIPMENT_TAGS", 1)[1].split("];", 1)[0]
    return re.findall(r'tag:\s*"([a-z_]+)"', block)


def test_web_configured_full_gym_gaps_are_listed(catalog, matrix: list[KitOutcome]) -> None:
    web = _web_picker_tags()
    gaps: dict[tuple[str, str], str] = {}
    for domain, category, binding in bindings():
        full = next(r for r in matrix
                    if (r.domain, r.category, r.kit) == (domain, category, "full_gym"))
        if not full.ok:
            continue
        via_web = evaluate(domain, category, binding, "full_gym", catalog, equipment=web)
        if not via_web.ok:
            gaps[(domain, category)] = via_web.outcome
    assert gaps == UI_UNSUPPORTED


def test_every_binding_slug_has_a_plan_label() -> None:
    """``plan:session_replaced=<slug>`` renders "Another planning rule was applied." without one."""
    missing = sorted({b.slug for _, _, b in bindings()} - set(_PLAN_SLOTS))
    assert missing == []
