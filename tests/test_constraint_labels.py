"""Every code the prescriber can write into ``constraints_applied`` has a reviewed label (S-A).

The Planning tab used to print engine codes verbatim (``block:phase=accumulation(×1.15)``,
``equipment:fallback_bodyweight``). ``app.logic.constraint_labels`` now words them. These tests
hold the three rules that make the words honest:

* coverage is established from the EMITTERS — the prescriber source, the validator's failure
  messages, the program templates' rule ids, the safety branches — not from a format check;
* an unrecognised code gets an honest fallback, never a label assembled from its punctuation;
* bookkeeping is hidden, but anything that replaced the session is always visible.

Pure: no database.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import constraint_labels as cl
from app.logic.constraint_labels import UNKNOWN_LABEL, describe_constraint, describe_constraints
from app.logic.prescriber import recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

ROOT = Path(__file__).resolve().parents[1]
PRESCRIBER_SOURCE = (ROOT / "app/logic/prescriber.py").read_text(encoding="utf-8")
VALIDATOR_SOURCE = (ROOT / "app/logic/constraint_engine/constraints_impl.py").read_text(encoding="utf-8")

#: One code per emitter family, formatted exactly as its emitter formats it.
EXAMPLES: dict[str, str] = {
    "planning:infeasible": "planning:infeasible",
    "constraint:": "constraint:exclude_modality=Running:user_rest_day",
    "constraint_soft:": "constraint_soft:exclude_session_type=Intervals:prefer_easy_week",
    "static_with_safety_caps:arm": "static_with_safety_caps:arm",
    "deload_need:": "deload_need:bias(shadow)=0.61",
    "weak_point:": "weak_point:posterior_chain",
    "objective:domain_emphasis=": "objective:domain_emphasis=hypertrophy",
    "objective:taper(": "objective:taper(×0.60)",
    "block:phase=": "block:phase=accumulation(×1.15)",
    "block:rpe_target=": "block:rpe_target=6.5-7.5",
    "block:periodization=": "block:periodization=generic",
    "block:deload(": "block:deload(×0.50)",
    "block:benchmark": "block:benchmark",
    "block:accessories=": "block:accessories=balanced(+2)",
    "block:target_duration=": "block:target_duration=45",
    "block:intensity=": "block:intensity=hard",
    "adherence:recent_skips=": "adherence:recent_skips=3",
    "adherence:recent_modifications=": "adherence:recent_modifications=2",
    "safety:override=": "safety:override=safety_regional_tissue",
    cl.PLAN_FOLLOWED_PREFIX: "plan:session_followed=hyp_upper_split",
    cl.PLAN_REPLACED_PREFIX: "plan:session_replaced=hypertrophy_upper(readiness)",
    cl.EQUIPMENT_UNCONFIGURED: cl.EQUIPMENT_UNCONFIGURED,
    cl.EQUIPMENT_FILTERED: cl.EQUIPMENT_FILTERED,
    cl.EQUIPMENT_BODYWEIGHT_ONLY: cl.EQUIPMENT_BODYWEIGHT_ONLY,
    cl.EQUIPMENT_FALLBACK_BODYWEIGHT: cl.EQUIPMENT_FALLBACK_BODYWEIGHT,
    cl.EQUIPMENT_ACCESSORIES_SKIPPED_PREFIX: "equipment:accessories_skipped=1",
    cl.EQUIPMENT_PREFERENCE_PREFIX: "equipment:preference=dumbbell,machine(changed=2)",
}

#: Bookkeeping families that never shaped the session and so are not shown.
HIDDEN_FAMILIES = {"static_with_safety_caps:arm"}


def _emitted_heads() -> set[str]:
    """The stable head of every code literal the prescriber appends to ``constraints_applied``."""
    literals = re.findall(
        r'constraints_applied\.(?:append|extend)\(\s*\[?\s*f?"([a-z_]+:[a-z_]*[=(]?)',
        PRESCRIBER_SOURCE,
    )
    return set(literals)


def test_every_emitted_code_family_is_labelled() -> None:
    heads = _emitted_heads()
    assert heads, "the emitter scan found nothing — the regex no longer matches the prescriber"
    unlabelled = sorted(h for h in heads if h not in cl.CODE_FAMILIES)
    assert not unlabelled, f"prescriber emits code families with no reviewed label: {unlabelled}"


def test_every_labelled_family_has_a_real_label() -> None:
    assert set(EXAMPLES) == set(cl.CODE_FAMILIES)
    for family, code in EXAMPLES.items():
        entry = describe_constraint(code)
        assert entry.label != UNKNOWN_LABEL, family
        assert entry.code == code
        assert entry.athlete_visible == (family not in HIDDEN_FAMILIES), family


def test_every_validator_failure_message_is_labelled() -> None:
    messages = re.findall(
        r'_fail\(\s*"[a-z_0-9]+",\s*(?:hard=False,\s*)?msg="([^"]+)"', VALIDATOR_SOURCE
    )
    assert len(messages) >= 20, "the validator scan no longer matches constraints_impl.py"
    unlabelled = [m for m in messages if describe_constraint(m).label == UNKNOWN_LABEL]
    assert not unlabelled, unlabelled
    # The one formatted message, as the validator renders it.
    tissue = describe_constraint("tissue stress elevated (lumbar 71, knee 40)")
    assert tissue.label == "Tissue stress is elevated (lumbar 71, knee 40)."


def test_every_safety_branch_has_its_own_label() -> None:
    branches = re.findall(r'branch_id="(safety_[a-z_]+)"', PRESCRIBER_SOURCE)
    assert branches
    generic = describe_constraint("safety:override=safety_not_a_branch").label
    for branch in branches:
        entry = describe_constraint(f"safety:override={branch}")
        assert entry.group == "safety" and entry.athlete_visible
        assert entry.label != generic, branch


def test_template_rule_ids_are_described_as_checked_and_hidden() -> None:
    data = json.loads((ROOT / "app/data/program_templates.json").read_text(encoding="utf-8"))
    rule_ids: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "constraint_rule_ids":
                    rule_ids.update(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    assert rule_ids
    for rule in rule_ids:
        entry = describe_constraint(rule)
        # Listed whether or not it fired: a rule that was checked is not an adjustment.
        assert (entry.group, entry.athlete_visible) == ("internal", False), rule
        assert entry.label != UNKNOWN_LABEL


def test_an_unrecognised_code_gets_the_honest_fallback() -> None:
    for code in ("mystery:thing=3", "block:phase=warp_speed(×9.00)", "weak_point:not_a_tag"):
        entry = describe_constraint(code)
        assert entry.label == UNKNOWN_LABEL, code
        assert entry.athlete_visible and entry.group == "other"


def test_a_hard_validator_failure_is_a_visible_replacement_and_a_soft_one_is_advisory() -> None:
    message = "Readiness low for max/competition intensity"
    hard = describe_constraints([message], hard_violations=[message])[0]
    soft = describe_constraints([message])[0]
    assert (hard.group, hard.athlete_visible) == ("safety", True)
    assert "replaced" in hard.label
    assert (soft.group, soft.athlete_visible) == ("advisory", True)
    assert "replaced" not in soft.label


def test_labels_say_what_the_engine_did() -> None:
    # The envelope scales duration_min, so the factor is a session-length factor.
    assert describe_constraint("block:phase=accumulation(×1.15)").label == (
        "Accumulation phase: session length ×1.15."
    )
    # A soft plan preference removed nothing, and says so.
    assert "removed no option" in describe_constraint(
        "constraint_soft:exclude_session_type=Intervals:prefer_easy_week"
    ).label
    # Only the scorer-acted tier is shown; the others are explanation-only.
    assert describe_constraint("deload_need:bias(shadow)=0.60").athlete_visible
    assert not describe_constraint("deload_need:watch(shadow)=0.40").athlete_visible
    assert describe_constraint("equipment:preference=dumbbell(changed=0)").label == (
        "Preference: dumbbells. No exercise choice changed."
    )


def _state(*, lumbar: float = 0.0, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=50.0)
    f = FatigueState(muscular=muscular)
    t = TissueState(lumbar=lumbar)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={"squat": 0.5},
        **leg,
    )


@pytest.mark.parametrize("goal", ["Strength", "Hypertrophy", "Powerlifting", "Running"])
def test_every_prescription_labels_each_of_its_codes(catalog_snapshot, goal) -> None:
    rx = recommend_next_session(
        _state(),
        goal=goal,
        available_equipment=["barbell", "dumbbells"],
        active_weak_points=["posterior_chain"],
        block_context={"week_number": 2, "duration_weeks": 6, "accessory_emphasis": "balanced"},
        catalog=catalog_snapshot,
    )
    assert rx.why is not None
    assert [d.code for d in rx.why.constraint_details] == rx.why.constraints_applied
    unknown = [d.code for d in rx.why.constraint_details if d.label == UNKNOWN_LABEL]
    assert not unknown, unknown


def test_a_safety_override_is_shown_as_an_applied_adjustment(catalog_snapshot) -> None:
    rx = recommend_next_session(_state(lumbar=70.0), goal="Strength", catalog=catalog_snapshot)
    assert rx.why is not None
    safety = [d for d in rx.why.constraint_details if d.group == "safety"]
    assert safety and all(d.athlete_visible for d in safety)
    codes = [d.code for d in safety]
    assert "safety:override=safety_regional_tissue" in codes
    # The same state fails the universal tissue rule, which replaced the session too — shown.
    assert any("replaced" in d.label for d in safety if d.code.startswith("tissue stress"))


def test_the_workload_preference_says_what_it_did_and_what_it_did_not() -> None:
    """Every no-op form is labelled: a preference that quietly did nothing is the failure mode."""
    applied = describe_constraint("block:intensity=hard")
    assert applied.athlete_visible and applied.group == "block"
    assert "working sets" in applied.label

    for code, expected in (
        ("block:intensity=hard(no-op:recovery-week)", "recovery weeks"),
        ("block:intensity=hard(no-op:no-set-targets:running)", "no set targets"),
        ("block:intensity=easy(no-op:sets-at-floor)", "one set per exercise"),
    ):
        entry = describe_constraint(code)
        assert entry.label != UNKNOWN_LABEL, code
        assert expected in entry.label, (code, entry.label)
