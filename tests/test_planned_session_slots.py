"""The plan → template bindings are real, and every planned slot is accounted for.

Pure, no DB. Two things matter here and neither is about scoring:

* every branch id a binding names exists in the pool that domain actually prescribes from —
  a renamed template must fail here rather than silently becoming "no binding";
* every category the planner can write is either bound or a stated gap, so a new slot cannot
  be added without someone deciding what it means.
"""
from __future__ import annotations

import pytest

from app.logic.candidate_library import GENERAL_TEMPLATES, GOAL_TEMPLATE_LIBRARY
from app.logic.planned_session_slots import _BINDINGS, binding_for
from app.services.planning_service import _DEFAULT_TEMPLATES, _DOMAIN_SLOT

#: Slots the planner writes that bind no template. Previously an accepted allowlist; from the
#: engine-coherence work these are DEFECTS awaiting phases 5 (running, power) and 6 (the three
#: mixed/HYROX/CrossFit days). A day the planner shows an athlete and then cannot prescribe
#: falls through to a generic pool — the "Running day prescribes Air Squat" failure.
#: Kept only so the phase-0 xfail can name exactly what is outstanding; delete entries as each
#: is closed, and delete this table when it is empty.
OUTSTANDING_UNBOUND_SLOTS: dict[str, set[str]] = {
    "running": {"Active Recovery"},
    "power": {"Strength Potentiation"},
    "mixed": {"Strength + Skill", "Running + Functional", "Hyrox Simulation"},
}


def _pool_branch_ids(domain: str) -> set[str]:
    """The branch ids `get_templates` can return for a domain, eligibility aside."""
    pool = GOAL_TEMPLATE_LIBRARY.get(domain, GENERAL_TEMPLATES)
    return {t.branch_id for t in pool}


@pytest.mark.parametrize("domain", sorted(_BINDINGS))
def test_every_bound_branch_exists_in_that_domains_pool(domain: str) -> None:
    available = _pool_branch_ids(domain)
    for category, binding in _BINDINGS[domain].items():
        missing = [b for b in binding.branch_ids if b not in available]
        assert not missing, f"{domain}/{category} binds unknown template(s): {missing}"


def test_bindings_name_at_least_one_template_each() -> None:
    for domain, slots in _BINDINGS.items():
        for category, binding in slots.items():
            assert binding.branch_ids, f"{domain}/{category} binds nothing"
            assert binding.slug and binding.slug.islower(), f"{domain}/{category} slug not a slug"


def _unbound_planned_slots() -> list[str]:
    """Every category the planner can write that binds no template."""
    from app.logic.domain_vocab import canonical_domain

    unresolved: list[str] = []
    for goal, slots in _DEFAULT_TEMPLATES.items():
        domain = canonical_domain(goal.value)
        for slot in slots:
            if binding_for(domain, slot.category) is None:
                unresolved.append(f"{domain}/{slot.category}")
    for domain, (category, _modality) in _DOMAIN_SLOT.items():
        if binding_for(domain, category) is None:
            unresolved.append(f"{domain}/{category}")
    return sorted(set(unresolved))


@pytest.mark.xfail(
    reason="phases 5-6: 5 planned slots bind no template (running Active Recovery, power "
    "Strength Potentiation, both HYROX days, CrossFit Strength + Skill), so the prescriber "
    "falls through to the generic pool for a day the athlete was shown",
    strict=True,
)
def test_every_planned_slot_is_bound() -> None:
    """A day the planner can schedule must be a day the prescriber can build.

    This was an allowlist (KNOWN_GAPS) — a slot could be "a stated gap" forever. A stated gap
    is still an athlete receiving general-purpose work on a day labelled as their sport.
    """
    assert _unbound_planned_slots() == []


def test_the_outstanding_gap_list_matches_reality() -> None:
    """The xfail above must stay honest: no gap may appear that is not already declared.

    This test PASSES today and is the ratchet — a newly added unbound slot fails here
    immediately rather than hiding inside the known failure above.
    """
    declared = {
        f"{domain}/{category}"
        for domain, categories in OUTSTANDING_UNBOUND_SLOTS.items()
        for category in categories
    }

    assert set(_unbound_planned_slots()) <= declared, (
        "a NEW unbound planned slot appeared: "
        f"{sorted(set(_unbound_planned_slots()) - declared)}"
    )


def test_every_binding_can_actually_produce_a_template() -> None:
    """Bound-but-never-eligible is the next failure mode after bound-but-missing.

    A binding names branch ids; this asserts each of those templates is reachable in its
    domain's pool at all. Eligibility for a given athlete is scored elsewhere — the claim
    here is only that the binding is not pointing at something the pool never offers.
    """
    empty: list[str] = []
    for domain, slots in _BINDINGS.items():
        available = _pool_branch_ids(domain)
        for category, binding in slots.items():
            if not set(binding.branch_ids) & available:
                empty.append(f"{domain}/{category}")

    assert not empty, f"bindings whose templates are unreachable in their pool: {empty}"


def test_no_branch_id_is_declared_twice() -> None:
    """Duplicate ids make "which template won?" unanswerable from the explanation alone."""
    from collections import Counter

    counts: Counter[str] = Counter()
    seen: set[int] = set()
    for pool in GOAL_TEMPLATE_LIBRARY.values():
        for template in pool:
            if id(template) in seen:
                continue
            seen.add(id(template))
            counts[template.branch_id] += 1

    duplicates = sorted(bid for bid, n in counts.items() if n > 1)
    assert not duplicates, f"branch ids declared more than once: {duplicates}"


def test_no_binding_without_a_category_or_for_an_unknown_domain() -> None:
    assert binding_for("hypertrophy", None) is None
    assert binding_for("hypertrophy", "") is None
    assert binding_for("hypertrophy", "Benchmark Session") is None
    assert binding_for("underwater_basket_weaving", "High Volume Upper") is None


def test_the_hypertrophy_week_binds_to_its_three_templates() -> None:
    upper = binding_for("hypertrophy", "High Volume Upper")
    lower = binding_for("hypertrophy", "High Volume Lower")
    accessory = binding_for("hypertrophy", "Accessory / Isolation")
    assert upper is not None and upper.branch_ids == ("hyp_upper_split",)
    assert lower is not None and lower.branch_ids == ("hyp_high_vol",)
    assert accessory is not None and accessory.branch_ids == ("hyp_maintenance",)
