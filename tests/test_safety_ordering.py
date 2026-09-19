"""Safety must be a ranking, not a source-code accident (phase 0 harness).

``_safety_candidates`` (app/logic/prescriber.py:230-293) appends up to four override
candidates in the order they happen to be written, and the caller uses ``safety[0]``
(:1022). There is no declared severity: the winner is whichever rule the author typed first.

Three consequences, each a test below:

* **Ranking is positional.** Regional tissue (:234) is written before systemic metabolic
  (:280), so an athlete who is both critically fatigued AND has sore knees is sent to
  "Swim / Bike Easy" rather than to rest. Reordering the source would change the
  prescription for a real athlete with no other change.
* **Complete rest is the weakest instruction.** The passive-rest candidate is appended last
  and can never win against any other trigger.
* **A hard override can be replaced by something LESS restrictive.** Passive rest carries
  ``duration_min=0``; ``finalize_prescription`` then evaluates
  ``min(rx.duration_min, 35) if rx.duration_min else 30``
  (app/logic/prescription_finalize.py:405) — a falsy-zero pun that turns "do not train" into
  a 30-minute session.

Physiological assumption under test: systemic autonomic overload outranks a regional tissue
substitution. You can swim on a sore knee; you cannot swim your way out of systemic fatigue.
The ordering is a clinical claim and belongs in a declared table, not in append order.
"""
from __future__ import annotations

import pytest
from test_prescriber_candidates import _state

from app.logic.prescriber import _safety_candidates, recommend_next_session

GOAL = "Strength"

#: State that trips BOTH the regional-tissue rule (knee > 70) and the systemic rule
#: (f_met_systemic > 80). Exactly the athlete whose ranking must be principled.
def _systemically_fried_with_sore_knees():
    return _state(knee=85.0, f_met_systemic=92.0)


def _branch_ids(state) -> list[str]:
    return [c.branch_id for c in _safety_candidates(state)]


def _codes(rx) -> list[str]:
    return list(rx.why.constraints_applied) if rx.why is not None else []


def test_the_multi_trigger_state_really_does_trip_several_rules() -> None:
    """Precondition for everything below: this athlete matches more than one rule."""
    ids = _branch_ids(_systemically_fried_with_sore_knees())

    assert "safety_systemic_metabolic" in ids
    assert "safety_regional_tissue" in ids
    assert len(ids) >= 2


def test_passive_rest_outranks_a_tissue_substitution() -> None:
    """Systemically overloaded ⇒ rest, even if a regional rule also fires."""
    rx = recommend_next_session(_systemically_fried_with_sore_knees(), goal=GOAL)

    assert "safety:override=safety_systemic_metabolic" in _codes(rx), _codes(rx)


def test_the_chosen_override_is_independent_of_rule_evaluation_order() -> None:
    """The same state must yield the same instruction whatever order the rules are checked.

    The engine ranks by ``SAFETY_SEVERITY``; this test ranks by its OWN table, written as a
    clinical statement independent of the engine's, and requires the two to agree.
    """
    state = _systemically_fried_with_sore_knees()
    candidates = _safety_candidates(state)

    # Severity as a clinical statement, independent of where the rules were typed.
    severity = {
        "safety_systemic_metabolic": 3,
        "safety_structural_damage": 2,
        "safety_regional_tissue": 1,
        "safety_tendon_structural": 0,
    }
    most_severe = max(candidates, key=lambda c: severity[c.branch_id])

    assert candidates[0].branch_id == most_severe.branch_id


def test_a_hard_override_is_never_replaced_by_something_less_restrictive() -> None:
    """The one-way ratchet: a later stage may restrict further, never relax.

    Driven through the real prescriber so the finalize stage participates.
    """
    state = _state(f_met_systemic=95.0, lumbar=88.0, knee=88.0, f_struct_damage=85.0)
    rx = recommend_next_session(state, goal=GOAL)

    emitted = [c for c in _safety_candidates(state) if c.branch_id == "safety_systemic_metabolic"]
    assert emitted, "precondition: passive rest was triggered"
    assert rx.duration_min == 0, (
        f"passive rest (duration_min=0) was relaxed into a {rx.duration_min}-minute session"
    )


def test_a_safety_override_is_always_reported_to_the_athlete() -> None:
    """Whatever wins, the athlete is told an override happened (holds today — keep it)."""
    rx = recommend_next_session(_systemically_fried_with_sore_knees(), goal=GOAL)

    assert any(c.startswith("safety:override=") for c in _codes(rx)), _codes(rx)


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("lumbar just over", {"lumbar": 65.01}),
        ("knee just over", {"knee": 70.01}),
        ("systemic just over", {"f_met_systemic": 80.01}),
        ("tendon just over", {"tendon": 55.01}),
    ],
)
def test_each_threshold_is_exclusive_at_its_boundary(label: str, kwargs: dict) -> None:
    """Boundary conditions: a rule fires just above its threshold and not just below it."""
    over = _safety_candidates(_state(**kwargs))
    under_kwargs = {k: v - 0.02 for k, v in kwargs.items()}
    under = _safety_candidates(_state(**under_kwargs))

    assert over, f"{label}: no override just above the threshold"
    assert not under, f"{label}: an override fired just below the threshold"


def test_every_safety_branch_the_engine_emits_declares_a_severity() -> None:
    """A new hard-stop rule without a declared severity would be ranked by accident again."""
    import re as _re
    from pathlib import Path

    from app.logic.prescriber import SAFETY_SEVERITY

    source = (Path(__file__).resolve().parents[1] / "app/logic/prescriber.py").read_text("utf-8")
    emitted = set(_re.findall(r'branch_id="(safety_[a-z_]+)"', source))

    assert emitted, "precondition: the scan found the safety branches"
    assert emitted == set(SAFETY_SEVERITY), (
        f"undeclared: {sorted(emitted - set(SAFETY_SEVERITY))}; "
        f"declared but never emitted: {sorted(set(SAFETY_SEVERITY) - emitted)}"
    )


def test_complete_rest_is_the_most_restrictive_instruction() -> None:
    from app.logic.prescriber import SAFETY_SEVERITY

    assert SAFETY_SEVERITY["safety_systemic_metabolic"] == max(SAFETY_SEVERITY.values())


def test_the_ordinary_constraint_fallback_is_unchanged_for_a_training_session() -> None:
    """Boundary: only complete rest is protected from the fallback; a real session is still
    capped at 35 minutes of easy movement when a hard constraint fires."""
    state = _state(lumbar=88.0, knee=88.0)  # regional-tissue rule, 30-minute substitution
    rx = recommend_next_session(state, goal=GOAL)

    assert rx.duration_min > 0
    assert rx.duration_min <= 35
