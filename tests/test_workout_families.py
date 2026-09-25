"""Workout families expand into the one template pool, indistinguishably (phase 4.3).

A family is a session design shared by several templates that differ only in their variants.
The contract these tests hold it to: expansion reproduces ordinary ``CandidateTemplate``s with
their ``branch_id`` identities intact, members live in the single pool beside the remaining
literals (never a parallel pool), and a member differs from its siblings only in what its
variant declares.

The golden corpora (``test_prescription_goldens.py``, ``test_scoring_goldens.py``) prove that
converting a pair to a family changed no prescription, score, eligibility or ranking.
"""
from __future__ import annotations

from collections import Counter

import pytest

from app.logic.candidate_library import (
    GOAL_TEMPLATE_LIBRARY,
    RUN_AEROBIC_FAMILY,
    RUN_THRESHOLD_FAMILY,
    SBD_STRENGTH_FAMILY,
    WORKOUT_FAMILIES,
    FamilyVariant,
    WorkoutFamily,
)

#: What a variant may set. Everything else a member carries comes from its family.
VARIANT_FIELDS = {
    "branch_id", "rationale", "kpi_eligible", "state_eligible", "goal_eligible", "focus",
    "exercise_slots",
}
#: Variant fields that override a family field when set; None inherits the family's value.
OVERRIDES = {"focus", "exercise_slots"}


def _pool_ids(domain: str) -> list[str]:
    return [t.branch_id for t in GOAL_TEMPLATE_LIBRARY[domain]]


@pytest.mark.parametrize("family", WORKOUT_FAMILIES, ids=lambda f: f.family_id)
def test_every_member_is_in_its_domain_pool_exactly_once_and_in_variant_order(family: WorkoutFamily) -> None:
    """One pool: a member is an ordinary pool entry, reached by the same lookups as a literal."""
    pool = _pool_ids(family.domain)
    member_ids = [v.branch_id for v in family.variants]

    assert all(pool.count(b) == 1 for b in member_ids), pool
    positions = [pool.index(b) for b in member_ids]
    assert positions == sorted(positions), "members appear in variant order"


@pytest.mark.parametrize("family", WORKOUT_FAMILIES, ids=lambda f: f.family_id)
def test_expansion_reproduces_the_pool_members(family: WorkoutFamily) -> None:
    """Same values as the instances in the pool — expansion is deterministic."""
    by_id = {t.branch_id: t for t in GOAL_TEMPLATE_LIBRARY[family.domain]}

    for member in family.expand():
        pooled = by_id[member.branch_id]
        assert member.type == pooled.type and member.focus == pooled.focus
        assert member.rationale == pooled.rationale
        assert member.exercise_slots == pooled.exercise_slots
        assert member.scoring is pooled.scoring


@pytest.mark.parametrize("family", WORKOUT_FAMILIES, ids=lambda f: f.family_id)
def test_members_differ_only_in_what_their_variant_declares(family: WorkoutFamily) -> None:
    members = family.expand()
    shared = {
        "type", "focus", "duration_min", "goal_alignment", "tags", "domain", "scoring",
        "exercise_slots",
    }

    for member, variant in zip(members, family.variants, strict=True):
        for name in shared:
            override = getattr(variant, name, None) if name in OVERRIDES else None
            expected = getattr(family, name) if override is None else override
            if name in ("tags", "exercise_slots"):
                expected = list(expected)
            assert getattr(member, name) == expected, (variant.branch_id, name)
    assert set(FamilyVariant.__dataclass_fields__) == VARIANT_FIELDS


def test_family_and_variant_ids_are_unique() -> None:
    family_ids = Counter(f.family_id for f in WORKOUT_FAMILIES)
    variant_ids = Counter(v.branch_id for f in WORKOUT_FAMILIES for v in f.variants)

    assert all(n == 1 for n in family_ids.values()), family_ids
    assert all(n == 1 for n in variant_ids.values()), variant_ids


def test_members_do_not_share_a_mutable_slot_list() -> None:
    """Templates are mutable and families are frozen; editing one member must not edit another."""
    a, b = SBD_STRENGTH_FAMILY.expand()

    a.exercise_slots.pop()

    assert len(b.exercise_slots) == len(SBD_STRENGTH_FAMILY.exercise_slots)


@pytest.mark.parametrize(
    "kpi",
    [{}, {"pl_relative_total": 2.0}, {"pl_relative_total": 2.999},
     {"pl_relative_total": 3.0}, {"pl_relative_total": 4.0}],
    ids=["no_total", "2.0x", "just_under_3x", "exactly_3x", "4.0x"],
)
def test_the_sbd_variants_partition_athletes_by_relative_total(kpi: dict[str, float]) -> None:
    """Complementary predicates: every athlete gets exactly one SBD session, never two or none."""
    eligible = [
        m.branch_id for m in SBD_STRENGTH_FAMILY.expand()
        if m.kpi_eligible is None or m.kpi_eligible(kpi)
    ]

    assert len(eligible) == 1, eligible


@pytest.mark.parametrize(
    "kpi",
    [{}, {"run_fatigue_factor": 10.0}, {"run_fatigue_factor": 14.0},
     {"run_fatigue_factor": 14.01}, {"run_fatigue_factor": 20.0}],
    ids=["no_ff", "10", "exactly_14", "just_over_14", "20"],
)
def test_the_aerobic_base_variants_partition_athletes_by_fatigue_factor(kpi: dict[str, float]) -> None:
    eligible = [
        m.branch_id for m in RUN_AEROBIC_FAMILY.expand()
        if m.kpi_eligible is None or m.kpi_eligible(kpi)
    ]

    assert len(eligible) == 1, eligible


@pytest.mark.parametrize(
    ("goal", "kpi", "expected"),
    [
        ("HalfMarathon", {"run_fatigue_factor": 20.0}, ["run_threshold"]),
        ("FullMarathon", {}, ["run_threshold"]),
        ("5K", {"run_fatigue_factor": 20.0}, ["run_threshold_ff"]),
        # Not a partition, on purpose: this athlete is offered no threshold session.
        ("5K", {"run_fatigue_factor": 10.0}, []),
    ],
)
def test_threshold_work_follows_the_race_goal_then_fatigue_factor(
    goal: str, kpi: dict[str, float], expected: list[str]
) -> None:
    eligible = [
        m.branch_id for m in RUN_THRESHOLD_FAMILY.expand()
        if (m.kpi_eligible is None or m.kpi_eligible(kpi))
        and (m.goal_eligible is None or m.goal_eligible(goal))
    ]

    assert eligible == expected


def test_a_variant_focus_overrides_only_its_own_member() -> None:
    tempo, intervals = RUN_THRESHOLD_FAMILY.expand()

    assert tempo.focus == RUN_THRESHOLD_FAMILY.focus
    assert intervals.focus.startswith("4×5 min")
