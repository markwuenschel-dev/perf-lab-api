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

#: Slots this module deliberately leaves unbound, with the reason (see planned_session_slots).
KNOWN_GAPS: dict[str, set[str]] = {
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


def test_every_planned_slot_is_bound_or_a_stated_gap() -> None:
    """Categories the planner writes must be decided, not forgotten.

    A slot may be a gap — selection then stays as it was — but the gap is listed here, so
    adding a planning slot without deciding what it prescribes fails this test.
    """
    from app.logic.domain_vocab import canonical_domain

    unresolved: list[str] = []
    for goal, slots in _DEFAULT_TEMPLATES.items():
        domain = canonical_domain(goal.value)
        for slot in slots:
            if binding_for(domain, slot.category) is None and slot.category not in KNOWN_GAPS.get(
                domain, set()
            ):
                unresolved.append(f"{domain}/{slot.category}")
    for domain, (category, _modality) in _DOMAIN_SLOT.items():
        if binding_for(domain, category) is None and category not in KNOWN_GAPS.get(domain, set()):
            unresolved.append(f"{domain}/{category}")
    assert not unresolved, f"planned slots with no binding and no stated gap: {sorted(set(unresolved))}"


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
