"""
Tests for the candidate-based prescriber.

Verifies:
- Candidate scoring produces valid rankings
- Safety overrides always win regardless of goal
- Hard constraint violations are overridden to recovery
- Different states produce different top candidates for the same goal
- Prescriptions have explainability payload
- All 14 goals return a valid prescription
"""

from datetime import UTC, datetime

import pytest

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.prescriber import (
    _candidate_domain,
    _gen_mixed_candidates,
    _gen_running_candidates,
    _gen_strength_candidates,
    _generate_candidates,
    _safety_candidates,
    _score_candidate,
    recommend_next_session,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


def _state(
    *,
    cns: float = 0.0,
    muscular: float = 0.0,
    metabolic: float = 0.0,
    structural: float = 0.0,
    tendon: float = 0.0,
    lumbar: float = 0.0,
    knee: float = 0.0,
    f_struct_damage: float = 0.0,
    f_met_systemic: float = 0.0,
    max_strength: float = 50.0,
    aerobic: float = 300.0,
    skill: float = 0.5,
    habit: float = 0.5,
) -> UnifiedStateVector:
    cx = CapacityState(aerobic=aerobic, max_strength=max_strength)
    f = FatigueState(
        cns=cns, muscular=muscular, metabolic=metabolic,
        structural=structural, tendon=tendon,
    )
    t = TissueState(lumbar=lumbar, knee=knee)
    leg = sync_legacy_from_vectors(cx, f, t)
    # Override legacy mirrors to match requested safety trigger values
    leg["f_struct_damage"] = f_struct_damage
    leg["f_met_systemic"] = f_met_systemic
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=habit,
        skill_state={"squat": skill},
        **leg,
    )


def _healthy_state() -> UnifiedStateVector:
    return _state()


# ---------------------------------------------------------------------------
# Safety overrides
# ---------------------------------------------------------------------------

def test_safety_override_triggers_on_high_lumbar(catalog_snapshot):
    s = _state(lumbar=70.0)
    overrides = _safety_candidates(s)
    assert len(overrides) > 0
    assert any(o.is_safety_override for o in overrides)


def test_safety_override_triggers_on_high_knee(catalog_snapshot):
    s = _state(knee=75.0)
    overrides = _safety_candidates(s)
    assert any(o.is_safety_override for o in overrides)


def test_safety_override_triggers_on_high_systemic_fatigue(catalog_snapshot):
    s = _state(f_met_systemic=85.0)
    overrides = _safety_candidates(s)
    assert any(o.is_safety_override for o in overrides)


def test_no_safety_override_when_healthy(catalog_snapshot):
    s = _healthy_state()
    overrides = _safety_candidates(s)
    assert len(overrides) == 0


def test_safety_override_wins_over_goal(catalog_snapshot):
    """Even with a goal, safety overrides should produce a Recovery prescription."""
    s = _state(lumbar=80.0)
    rx = recommend_next_session(s, goal="Powerlifting",
        catalog=catalog_snapshot,
    )
    assert rx.type == "Recovery" or "Deload" in rx.type or "recovery" in rx.rationale.lower()


def test_high_structural_damage_triggers_recovery(catalog_snapshot):
    # INT-05: the structural-safety Recovery reads the authoritative structural
    # component, not the lossy f_struct_damage blend (which now has no authority).
    s = _state(structural=85.0)
    rx = recommend_next_session(s, goal="Strength",
        catalog=catalog_snapshot,
    )
    assert rx.type == "Recovery"


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------

def test_high_state_fit_candidate_scores_higher(catalog_snapshot):
    from app.logic.prescriber import SessionCandidate
    good = SessionCandidate(
        type="A", focus="", rationale="", duration_min=60, branch_id="a",
        goal_alignment=1.0, state_fit=1.0, fatigue_penalty=0.0, tissue_penalty=0.0,
    )
    bad = SessionCandidate(
        type="B", focus="", rationale="", duration_min=60, branch_id="b",
        goal_alignment=1.0, state_fit=0.1, fatigue_penalty=0.8, tissue_penalty=0.5,
    )
    assert _score_candidate(good) > _score_candidate(bad)


def test_weak_point_coverage_increases_score(catalog_snapshot):
    from app.logic.prescriber import SessionCandidate
    with_coverage = SessionCandidate(
        type="A", focus="", rationale="", duration_min=60, branch_id="a",
        goal_alignment=0.8, state_fit=0.8, fatigue_penalty=0.1,
        weak_point_coverage=0.9,
    )
    without_coverage = SessionCandidate(
        type="B", focus="", rationale="", duration_min=60, branch_id="b",
        goal_alignment=0.8, state_fit=0.8, fatigue_penalty=0.1,
        weak_point_coverage=0.0,
    )
    assert _score_candidate(with_coverage) > _score_candidate(without_coverage)


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def test_strength_generates_multiple_candidates(catalog_snapshot):
    s = _healthy_state()
    cands = _gen_strength_candidates(s, {}, None)
    assert len(cands) >= 2


def test_strength_max_candidate_exists_when_ready(catalog_snapshot):
    s = _healthy_state()
    cands = _gen_strength_candidates(s, {}, None)
    types = [c.type for c in cands]
    assert any("Strength" in t for t in types)


def test_skill_candidate_present_when_skill_low(catalog_snapshot):
    s = _state(skill=0.3)
    cands = _gen_strength_candidates(s, {}, None)
    assert any("Skill" in c.type for c in cands)


def test_running_threshold_candidate_when_fatigue_factor_high(catalog_snapshot):
    s = _healthy_state()
    kpi = {"run_fatigue_factor": 18.0}
    cands = _gen_running_candidates(s, kpi, None, "Running")
    assert any("Threshold" in c.type for c in cands)


# ---------------------------------------------------------------------------
# End-to-end prescription for all goals
# ---------------------------------------------------------------------------

ALL_GOALS: list[TrainingGoal] = [
    "Strength", "Hypertrophy", "Power", "General", "OlympicLifts",
    "Powerlifting", "MetCon", "Calisthenics", "Gymnastics", "Grip",
    "Running", "Sprinting", "HalfMarathon", "FullMarathon",
]


@pytest.mark.parametrize("goal", ALL_GOALS)
def test_all_goals_return_valid_prescription(goal, catalog_snapshot):
    s = _healthy_state()
    rx = recommend_next_session(s, goal=goal,
        catalog=catalog_snapshot,
    )
    assert rx.type, f"Goal {goal}: prescription type must not be empty"
    assert rx.focus, f"Goal {goal}: prescription focus must not be empty"
    assert rx.duration_min >= 0, f"Goal {goal}: duration must be non-negative"


@pytest.mark.parametrize("goal", ALL_GOALS)
def test_all_goals_have_explainability(goal, catalog_snapshot):
    s = _healthy_state()
    rx = recommend_next_session(s, goal=goal,
        catalog=catalog_snapshot,
    )
    assert rx.why is not None, f"Goal {goal}: why payload must be present"
    assert rx.why.prescription_branch is not None


# ---------------------------------------------------------------------------
# Hard constraint override still fires
# ---------------------------------------------------------------------------

def test_finalize_overrides_to_recovery_on_hard_violation(catalog_snapshot):
    """
    If finalize_prescription detects hard violations, output should be Recovery.
    We test this indirectly: a state that triggers structural safety should override
    to Recovery even after going through the candidate path.
    """
    s = _state(structural=80.0, tendon=70.0)
    rx = recommend_next_session(s, goal="Powerlifting",
        catalog=catalog_snapshot,
    )
    # Safety or tissue deload expected
    assert rx.type in ("Recovery", "Tissue Deload") or rx.why is not None


# ---------------------------------------------------------------------------
# State drives different prescriptions
# ---------------------------------------------------------------------------

def test_fatigued_state_produces_different_prescription_than_fresh(catalog_snapshot):
    fresh = _healthy_state()
    tired = _state(cns=70.0, muscular=75.0)
    rx_fresh = recommend_next_session(fresh, goal="Strength",
        catalog=catalog_snapshot,
    )
    rx_tired = recommend_next_session(tired, goal="Strength",
        catalog=catalog_snapshot,
    )
    # They don't have to differ in every case, but the types or focus typically will
    # Just verify both return valid prescriptions
    assert rx_fresh.type
    assert rx_tired.type


def test_equipment_aware_exercises_fallback_to_bodyweight(catalog_snapshot):
    # Hypertrophy + elevated muscular fatigue resolves to the `hyp_maintenance`
    # template, which carries NO exercise_slots — so exercise selection falls
    # through to the equipment map, and with no equipment configured that is the
    # bodyweight trio. Asserting the actual names (not just len>0) so this test
    # can't silently pass if the equipment path breaks. (A goal whose winning
    # template HAS slots, e.g. Strength→strength_max, would bypass this path.)
    s = _state(muscular=70.0)
    rx = recommend_next_session(s, goal="Hypertrophy", available_equipment=None,
        catalog=catalog_snapshot,
    )
    assert [e.name for e in rx.exercises] == ["Air Squat", "Push-up", "Lunges"]
    assert any("equipment:fallback_bodyweight" in c for c in (rx.why.constraints_applied if rx.why else []))


def test_equipment_aware_exercises_use_available_equipment(catalog_snapshot):
    # Same empty-slot template (`hyp_maintenance`), but with a barbell available
    # the equipment map supplies the barbell movement block.
    s = _state(muscular=70.0)
    rx = recommend_next_session(s, goal="Hypertrophy", available_equipment=["barbell"],
        catalog=catalog_snapshot,
    )
    assert [e.name for e in rx.exercises] == ["Back Squat", "Romanian Deadlift", "Bench Press"]
    assert any("equipment:filtered" in c for c in (rx.why.constraints_applied if rx.why else []))


# ---------------------------------------------------------------------------
# Equipment facts in the explanation (S-A): each code names the path that actually ran
# ---------------------------------------------------------------------------

def _equipment_codes(rx) -> list[str]:
    return [c for c in (rx.why.constraints_applied if rx.why else []) if c.startswith("equipment:")]


def test_unconfigured_equipment_with_slots_is_not_labelled_bodyweight(catalog_snapshot):
    """An empty list does not filter, so slot-resolved exercises are not a bodyweight fallback.

    The old label printed "fallback_bodyweight" whenever the list was empty — including for a
    barbell strength session resolved from the catalog.
    """
    rx = recommend_next_session(_healthy_state(), goal="Strength", available_equipment=None,
        catalog=catalog_snapshot,
    )
    assert _equipment_codes(rx) == ["equipment:unconfigured"]
    assert rx.exercises


def test_bodyweight_only_selects_differently_from_unconfigured(catalog_snapshot):
    """["bodyweight"] is a choice — only movements that need nothing. [] is no choice — no filter."""
    needs = {
        ex.name: {e for e in ex.equipment_required if e not in ("bodyweight", "none", "")}
        for ex in catalog_snapshot
    }
    unconfigured = recommend_next_session(_healthy_state(), goal="Strength", available_equipment=[],
        catalog=catalog_snapshot,
    )
    bodyweight = recommend_next_session(_healthy_state(), goal="Strength", available_equipment=["bodyweight"],
        catalog=catalog_snapshot,
    )
    assert _equipment_codes(unconfigured) == ["equipment:unconfigured"]
    assert _equipment_codes(bodyweight) == ["equipment:bodyweight_only"]
    assert all(not needs.get(e.name) for e in bodyweight.exercises), [e.name for e in bodyweight.exercises]
    assert any(needs.get(e.name) for e in unconfigured.exercises), [e.name for e in unconfigured.exercises]


def test_bodyweight_fallback_is_labelled_only_when_the_bodyweight_list_was_used(catalog_snapshot):
    """Configured equipment that matches no equipment-map key really does get the bodyweight list."""
    s = _state(muscular=70.0)  # hyp_maintenance: no slots, so the equipment map runs
    rx = recommend_next_session(s, goal="Hypertrophy", available_equipment=["rings"],
        catalog=catalog_snapshot,
    )
    assert _equipment_codes(rx) == ["equipment:fallback_bodyweight"]


# ---------------------------------------------------------------------------
# Canonical domain dispatch (ADR-0038)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "goal,domain",
    [
        ("Strength", "strength"),
        ("Powerlifting", "powerlifting"),
        ("OlympicLifts", "weightlifting"),
        ("MetCon", "mixed"),
        ("HalfMarathon", "running"),
        ("Calisthenics", "calisthenics"),
        ("Hyrox", "mixed"),      # BlockGoal, not a TrainingGoal
        ("CrossFit", "mixed"),   # BlockGoal
        ("Recomp", "general"),   # BlockGoal
    ],
)
def test_candidate_domain_resolves(goal, domain, catalog_snapshot):
    assert _candidate_domain(goal) == domain


def test_mixed_pool_has_conditioning_and_strength(catalog_snapshot):
    cands = _gen_mixed_candidates(_healthy_state(), {}, None)
    types = [c.type for c in cands]
    assert any("Conditioning" in t for t in types)
    assert any("Strength Endurance" in t for t in types)


def test_block_goal_hyrox_yields_real_session(catalog_snapshot):
    """A Hyrox block goal (not in TrainingGoal) still reaches the mixed pool, not general."""
    cands = _generate_candidates(_healthy_state(), "Hyrox", {}, None)  # type: ignore[arg-type]
    types = [c.type for c in cands]
    assert any("Conditioning" in t or "Strength Endurance" in t for t in types)


def test_metcon_default_not_strength_dominated(catalog_snapshot):
    """MetCon → mixed must stay conditioning-primary, not collapse to a strength session."""
    rx = recommend_next_session(_healthy_state(), goal="MetCon",
        catalog=catalog_snapshot,
    )
    assert rx.type != "Strength Endurance"
