"""Every planned day × equipment kit, classified honestly (phase 9 harness).

For each binding in ``planned_session_slots`` and each kit in ``kit_support``, run the real
prescriber (in memory, against the seeder catalog; no database), then classify what the
athlete would get. Classification is from first principles, not from explanation codes. The
equipment-map path and a real catalog selection both emit ``equipment:filtered`` for a
configured kit, so the codes cannot tell them apart. The winning template's slots are
re-resolved against the same kit and catalog instead.

Outcomes:
* ``followed``: the planned template, every slot realized;
* ``replaced``: a different template of the SAME domain, every slot realized, stated;
* ``unavailable``: stated ``plan:session_unavailable`` (phase 9.4), zero work;
* ``filler``: a template with no slots, no slot realized (the generic equipment map), or a
  template from another domain;
* ``partial``: only some of an authored session's slots realized, which is a different session;
* ``other``: a safety or readiness path took the day (not expected for a fresh athlete).

Run: ``uv run python -m app.scripts.kit_matrix`` (markdown to stdout).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import cache
from typing import Literal

from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY, CandidateTemplate
from app.logic.exercise_slot import CatalogExercise, resolve_slots
from app.logic.kit_support import (
    BODYWEIGHT_EQUIPMENT,
    HOME_EQUIPMENT,
    KIT_SUPPORT,
    KITS,
    POOL_DOMAIN_ALIASES,
    Expect,
    Kit,
)
from app.logic.planned_session_slots import _BINDINGS, SlotBinding

Outcome = Literal["followed", "replaced", "unavailable", "filler", "partial", "other"]

#: What each expectation accepts. Filler and partial are never acceptable.
ACCEPTED: dict[Expect, frozenset[Outcome]] = {
    "fulfil": frozenset({"followed", "replaced"}),
    "replace": frozenset({"followed", "replaced"}),
    "unsupported": frozenset({"replaced", "unavailable"}),
}

#: Category-owned pools and the canonical domain they belong to.
_POOL_DOMAIN: dict[str, str] = {
    "sprinting": "running",
    "running_recovery": "running",
    "power_potentiation": "power",
    "hyrox_simulation": "mixed",
    "running_functional": "mixed",
    "strength_skill": "mixed",
}

#: The block goal an athlete on each planned domain's day would have.
_DOMAIN_GOAL: dict[str, str] = {
    "strength": "Strength",
    "hypertrophy": "Hypertrophy",
    "power": "Power",
    "running": "Running",
    "powerlifting": "Powerlifting",
    "weightlifting": "OlympicLifts",
    "mixed": "MetCon",
    "calisthenics": "Calisthenics",
    "gymnastics": "Gymnastics",
    "grip": "Grip",
    "general": "General",
    "conditioning": "General",
}


@dataclass(frozen=True)
class KitOutcome:
    domain: str
    category: str
    slug: str
    kit: Kit
    expect: Expect
    outcome: Outcome
    branch: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.outcome in ACCEPTED[self.expect]


@cache
def _template_index() -> dict[str, tuple[str, CandidateTemplate]]:
    out: dict[str, tuple[str, CandidateTemplate]] = {}
    for pool, templates in GOAL_TEMPLATE_LIBRARY.items():
        for t in templates:
            out[t.branch_id] = (_POOL_DOMAIN.get(pool, pool), t)
    return out


def kit_equipment(kit: Kit, catalog: list[CatalogExercise]) -> list[str] | None:
    """The equipment list a kit declares, as an athlete profile would store it."""
    if kit == "unconfigured":
        return None
    if kit == "full_gym":
        return sorted({e.strip().lower() for ex in catalog for e in ex.equipment_required if e})
    if kit == "home":
        return list(HOME_EQUIPMENT)
    return list(BODYWEIGHT_EQUIPMENT)


def bindings() -> list[tuple[str, str, SlotBinding]]:
    return [(d, c, b) for d, cats in _BINDINGS.items() for c, b in cats.items()]


def classify(
    domain: str,
    binding: SlotBinding,
    branch: str,
    codes: list[str],
    equipment: list[str] | None,
    catalog: list[CatalogExercise],
) -> tuple[Outcome, str]:
    if any(c.startswith("plan:session_unavailable=") for c in codes):
        return "unavailable", "stated unavailable"
    found = _template_index().get(branch)
    if found is None:
        return "other", f"branch {branch!r} is not a library template"
    tdomain, template = found
    planned = POOL_DOMAIN_ALIASES.get(domain, domain)
    if tdomain != planned:
        return "filler", f"cross-domain: {tdomain} template on a {planned} day"
    slots = list(template.exercise_slots or [])
    if not slots:
        return "filler", "template has no slots: generic equipment map"
    kit = (
        frozenset(e.strip().lower() for e in equipment if e and e.strip()) if equipment else None
    )
    realized = sum(1 for r in resolve_slots(slots, catalog, available_equipment=kit) if r.chosen)
    if realized == 0:
        return "filler", "no slot realized: generic equipment map"
    if realized < len(slots):
        return "partial", f"{realized}/{len(slots)} slots realized"
    followed = branch in binding.branch_ids and f"plan:session_followed={branch}" in codes
    return ("followed" if followed else "replaced"), f"{realized}/{len(slots)} slots"


def evaluate(
    domain: str,
    category: str,
    binding: SlotBinding,
    kit: Kit,
    catalog: list[CatalogExercise],
    equipment: list[str] | None = None,
) -> KitOutcome:
    from app.logic.prescriber import recommend_next_session
    from app.scripts import simulate_matrix as sm

    level_key, _ = sm.EXPERIENCE["intermediate"]
    state = sm._state(level_key, *sm.FRESHNESS["fresh"])
    goal = _DOMAIN_GOAL[domain]
    eq = kit_equipment(kit, catalog) if equipment is None else equipment
    rx = recommend_next_session(
        state,
        goal=goal,  # type: ignore[arg-type]
        catalog=catalog,
        available_equipment=eq,
        block_context={
            "block_goal": goal,
            "session_category": category,
            "session_domain": domain,
            "week_number": 2,
            "duration_weeks": 8,
            "deload_every_n_weeks": 4,
        },
    )
    codes = list(rx.why.constraints_applied) if rx.why else []
    branch = (rx.why.prescription_branch if rx.why else None) or ""
    outcome, detail = classify(domain, binding, branch, codes, eq, catalog)
    return KitOutcome(
        domain=domain, category=category, slug=binding.slug, kit=kit,
        expect=KIT_SUPPORT[binding.slug][kit], outcome=outcome, branch=branch, detail=detail,
    )


def build_kit_matrix(catalog: list[CatalogExercise] | None = None) -> list[KitOutcome]:
    from app.scripts import simulate_matrix as sm

    cat = catalog if catalog is not None else sm._catalog()
    return [
        evaluate(d, c, b, kit, cat)
        for d, c, b in bindings()
        for kit in KITS
    ]


_MARK: dict[Outcome, str] = {
    "followed": "✓", "replaced": "↻", "unavailable": "∅", "filler": "✗ filler",
    "partial": "✗ partial", "other": "?",
}


def render(rows: list[KitOutcome]) -> str:
    by_day: dict[tuple[str, str], dict[str, KitOutcome]] = {}
    for r in rows:
        by_day.setdefault((r.domain, r.category), {})[r.kit] = r
    head = "| domain | planned day | " + " | ".join(KITS) + " |"
    lines = [head, "|" + "---|" * (2 + len(KITS))]
    for (domain, category), cells in by_day.items():
        marks = []
        for kit in KITS:
            r = cells[kit]
            mark = f"{_MARK[r.outcome]} `{r.branch}`" if r.outcome != "unavailable" else "∅"
            marks.append(mark if r.ok else f"**{mark}** (expected {r.expect})")
        lines.append(f"| {domain} | {category} | " + " | ".join(marks) + " |")
    bad = [r for r in rows if not r.ok]
    lines += ["", f"{len(rows)} cells, {len(bad)} not honest."]
    return "\n".join(lines)


def main() -> None:
    argparse.ArgumentParser(description="Planned day × equipment kit, classified.").parse_args()
    print(render(build_kit_matrix()))


if __name__ == "__main__":
    main()
