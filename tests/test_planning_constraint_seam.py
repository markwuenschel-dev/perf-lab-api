"""The ADR-0064 closure test, plus the properties that make the seam worth having.

ADR-0064:107-108 defines the acceptance criterion for this seam in one sentence:

    "The seam is proven by a closure test — a synthetic external hard constraint can be
     injected and honored **without changing solver architecture**."

That is what `test_a_synthetic_external_hard_constraint_is_honored` asserts, and it is the
test that would have to be deleted for the seam to regress. The rest pin the properties a
constraint layer has to have to be trustworthy: soft never silently behaves like hard, an
exclusion states its reason, and an infeasible pool never falls through to work the
constraint forbade.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.planning_constraints import (
    AuthorityClass,
    ConstraintKind,
    ConstraintScope,
    Hardness,
    ResolvedPlanningConstraint,
    apply_constraints,
)
from app.logic.prescriber import recommend_next_session
from app.schemas.state import UnifiedStateVector


def _healthy_state() -> UnifiedStateVector:
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        c_met_aerobic=50.0,
        c_nm_force=50.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
        f_met_systemic=20.0,
        f_nm_peripheral=15.0,
        f_nm_central=20.0,
        f_struct_damage=10.0,
        s_struct_signal=20.0,
        habit_strength=0.6,
        skill_state={"squat": 0.7},
    )


def _constraint(
    kind: ConstraintKind = ConstraintKind.EXCLUDE_DOMAIN,
    *,
    target: str = "running",
    hardness: Hardness = Hardness.HARD,
    threshold: float | None = None,
    **kw,
) -> ResolvedPlanningConstraint:
    return ResolvedPlanningConstraint(
        kind=kind,
        hardness=hardness,
        authority_class=kw.pop("authority_class", AuthorityClass.USER_OVERRIDE),
        scope=kw.pop("scope", ConstraintScope.MICROCYCLE),
        reason_code=kw.pop("reason_code", "athlete_excluded"),
        source_type=kw.pop("source_type", "synthetic_external"),
        target=target,
        threshold=threshold,
        **kw,
    )


def _candidate(ctype: str = "Endurance", domain: str = "running", minutes: int = 60):
    return SessionCandidate(
        type=ctype,
        focus="f",
        rationale="r",
        duration_min=minutes,
        branch_id=f"b-{ctype}-{domain}",
        domain=domain,
    )


# --- THE CLOSURE TEST (ADR-0064:107) ----------------------------------------


def test_a_synthetic_external_hard_constraint_is_honored():
    """Inject a constraint from outside the planner; the planner obeys it.

    "External" is the load-bearing word. This constraint is built here in the test, from
    no repository row and no engine rule — exactly how P12 will hand `PlanningOverride`
    rows in. If this passes, adding user overrides is producing more constraints of this
    type, not editing selection.
    """
    state = _healthy_state()

    baseline = recommend_next_session(state, goal="Running")
    assert baseline.why is not None

    constrained = recommend_next_session(
        state,
        goal="Running",
        constraints=[_constraint(ConstraintKind.EXCLUDE_SESSION_TYPE, target=baseline.type)],
    )

    assert constrained.type != baseline.type, (
        "the session the constraint excluded was prescribed anyway"
    )
    assert constrained.why is not None
    assert any("constraint:" in c for c in constrained.why.constraints_applied)


def test_the_solver_is_unchanged_when_no_constraints_are_supplied():
    """The other half of "without changing solver architecture".

    A seam that alters behaviour when nothing is injected has changed the solver. Every
    existing athlete passes no constraints, so this pins that their prescription is
    byte-identical.
    """
    state = _healthy_state()
    without = recommend_next_session(state, goal="Running")
    with_empty = recommend_next_session(state, goal="Running", constraints=[])

    assert without.type == with_empty.type
    assert without.focus == with_empty.focus
    assert without.duration_min == with_empty.duration_min
    assert without.why is not None and with_empty.why is not None
    assert without.why.constraints_applied == with_empty.why.constraints_applied


# --- Hard vs soft ------------------------------------------------------------


def test_a_soft_constraint_records_its_hit_without_removing_anything():
    """Soft must not quietly behave as hard.

    There is no scored alternative and no tradeoff trace to trade a soft preference
    against yet, so the only honest behaviour is to note it and leave selection alone.
    Treating soft as hard is the specific failure this pins against.
    """
    pool = [_candidate("Endurance", "running"), _candidate("Strength", "strength")]
    app = apply_constraints(pool, [_constraint(hardness=Hardness.SOFT, target="running")])

    assert len(app.survivors) == 2
    assert app.exclusions == []
    assert [h.candidate_type for h in app.soft_hits] == ["Endurance"]
    assert app.infeasible is False


def test_a_hard_constraint_removes_only_what_it_matches():
    pool = [_candidate("Endurance", "running"), _candidate("Strength", "strength")]
    app = apply_constraints(pool, [_constraint(target="running")])

    assert [c.domain for c in app.survivors] == ["strength"]
    assert [e.candidate_type for e in app.exclusions] == ["Endurance"]


def test_an_exclusion_carries_a_reason_rather_than_vanishing():
    """A removed candidate that leaves no trace is indistinguishable from one never
    generated — which is how the current pre-candidate eligibility predicates behave."""
    pool = [_candidate("Endurance", "running")]
    app = apply_constraints(pool, [_constraint(target="running", reason_code="athlete_excluded")])

    assert app.reason_codes() == ["exclude_domain=running:athlete_excluded"]
    assert app.exclusions[0].constraint.source_type == "synthetic_external"
    assert app.exclusions[0].constraint.authority_class is AuthorityClass.USER_OVERRIDE


# --- Feasibility -------------------------------------------------------------


def test_an_empty_pool_with_no_exclusions_is_not_infeasible():
    """Generator failure and constraint infeasibility are different states.

    Collapsing them would report "your constraints are impossible" when in fact no
    candidate was ever produced.
    """
    app = apply_constraints([], [_constraint()])
    assert app.survivors == []
    assert app.infeasible is False


def test_excluding_everything_is_infeasible():
    pool = [_candidate("Endurance", "running")]
    app = apply_constraints(pool, [_constraint(target="running")])
    assert app.infeasible is True


def test_an_impossible_constraint_set_does_not_prescribe_the_forbidden_work():
    """The bypass this seam exists to prevent.

    With every candidate barred, the prescriber must not fall through to the general
    template pool or the hardcoded equipment map — either would prescribe exactly what
    was forbidden. ADR-0064:67: never bypass a constraint to fill the calendar.
    """
    state = _healthy_state()
    rx = recommend_next_session(
        state,
        goal="Running",
        constraints=[_constraint(ConstraintKind.MAX_DURATION_MIN, target="", threshold=0.0)],
    )

    assert rx.why is not None
    assert "planning:infeasible" in rx.why.constraints_applied
    assert rx.type == "Recovery"
    assert rx.duration_min <= 35


# --- Time bounds -------------------------------------------------------------


def test_a_constraint_outside_its_window_does_not_apply():
    pool = [_candidate("Endurance", "running")]
    now = datetime.now(UTC)
    expired = _constraint(
        target="running",
        effective_until=now - timedelta(days=1),
    )
    app = apply_constraints(pool, [expired], now=now)
    assert len(app.survivors) == 1
    assert app.exclusions == []


def test_a_constraint_with_no_window_always_applies():
    pool = [_candidate("Endurance", "running")]
    app = apply_constraints(pool, [_constraint(target="running")], now=datetime.now(UTC))
    assert app.survivors == []


@pytest.mark.parametrize(
    "kind,target,threshold,expect_excluded",
    [
        (ConstraintKind.EXCLUDE_DOMAIN, "running", None, True),
        (ConstraintKind.EXCLUDE_DOMAIN, "strength", None, False),
        (ConstraintKind.EXCLUDE_SESSION_TYPE, "endur", None, True),
        (ConstraintKind.MAX_DURATION_MIN, "", 30.0, True),
        (ConstraintKind.MAX_DURATION_MIN, "", 90.0, False),
    ],
)
def test_each_kind_decides_what_it_claims_to(kind, target, threshold, expect_excluded):
    """Every member of the closed vocabulary is exercised, so an unimplemented kind
    cannot sit in the enum looking supported."""
    pool = [_candidate("Endurance", "running", minutes=60)]
    app = apply_constraints(pool, [_constraint(kind, target=target, threshold=threshold)])
    assert bool(app.exclusions) is expect_excluded
