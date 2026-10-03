"""W1-c — a hard safety constraint that cannot be evaluated yields complete rest.

Before: an unregistered or crashing HARD constraint landed in `skipped_codes`, counted as a
pass, and the full session went out. Now it is recorded as `unevaluated_hard` and the
session is replaced by complete rest — nothing actionable: no exercises, no loads, no
structured workout — which no later stage may build back out. Also pinned here: the same
"replacement is final" rule for an EVALUATED hard violation, whose recovery override used to
pick up template exercises and the block's target duration after finalize.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.coaching_template_registry import get_structured_template_for_goal
from app.logic.constraint_engine.constraints_impl import CONSTRAINT_REGISTRY
from app.logic.constraint_engine.context_builder import build_constraint_context
from app.logic.constraint_engine.types import ConstraintResult
from app.logic.constraint_engine.validator import SessionValidator
from app.logic.prescriber import recommend_next_session
from app.main import app
from app.models.mesocycle import BlockGoal, BlockStatus, MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.services.state_service import initialize_athlete_state

sys.path.insert(0, str(Path(__file__).parent))
from test_prescriber_finalize import _healthy_state  # noqa: E402

HARD_CODE = "universal_fatigue_ok"  # a universal hard rule: runs on every session
SOFT_CODE = "universal_metcon_fatigue_stack"
# Block preferences that, before the fix, rebuilt a replaced session into a training session.
BUILD_OUT = {"target_session_minutes": 60, "accessory_emphasis": "high"}


def _raise(*_args: object) -> ConstraintResult:
    raise RuntimeError("constraint bug")


def _complete_rest_problems(rx, code: str) -> list[str]:
    """Every way `rx` falls short of complete rest for an unevaluated `code` (empty = none)."""
    v = rx.why.validation if rx.why is not None else None
    checks = {
        "type is Rest": rx.type == "Rest",
        "duration 0": rx.duration_min == 0,
        "no exercises": rx.exercises == [],
        "no structured workout": rx.structure is None,
        "validation not passed": v is not None and v.passed is False,
        "unevaluated code recorded": v is not None and v.unevaluated_hard == [code],
        # No forecast for a session that was not prescribed.
        "no expected outcomes": rx.why is not None and rx.why.expected_outcomes == [],
        "constraint annotated": rx.why is not None
        and f"safety:unevaluated={code}" in rx.why.constraints_applied,
        "rationale says why": "safety check could not" in rx.rationale,
    }
    return [name for name, ok in checks.items() if not ok]


# ── validator ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("how", ["crashes", "unregistered"])
def test_unevaluable_hard_rule_is_unevaluated_not_passed(monkeypatch, how):
    if how == "crashes":
        monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
    else:
        monkeypatch.delitem(CONSTRAINT_REGISTRY, HARD_CODE)

    report = SessionValidator(get_structured_template_for_goal("Running")).validate(
        {"type": "Endurance", "duration_min": 40}, build_constraint_context(_healthy_state(), None, "Running")
    )
    assert report.unevaluated_hard == [HARD_CODE]
    assert HARD_CODE not in report.skipped_codes
    assert report.ok is False


def test_crashing_soft_rule_still_only_skips(monkeypatch):
    monkeypatch.setitem(CONSTRAINT_REGISTRY, SOFT_CODE, _raise)

    report = SessionValidator(get_structured_template_for_goal("Running")).validate(
        {"type": "Endurance", "duration_min": 40}, build_constraint_context(_healthy_state(), None, "Running")
    )
    assert SOFT_CODE in report.skipped_codes
    assert report.unevaluated_hard == []


# ── prescriber ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("block_context", [None, BUILD_OUT], ids=["plain", "with-block-build-out"])
@pytest.mark.parametrize("how", ["crashes", "unregistered"])
def test_unevaluable_hard_rule_prescribes_complete_rest(monkeypatch, how, block_context):
    if how == "crashes":
        monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
    else:
        monkeypatch.delitem(CONSTRAINT_REGISTRY, HARD_CODE)
    rx = recommend_next_session(_healthy_state(), goal="Strength", block_context=block_context)
    assert _complete_rest_problems(rx, HARD_CODE) == []


def test_soft_rule_crash_does_not_change_the_session(monkeypatch):
    baseline = recommend_next_session(_healthy_state(), goal="Strength")
    monkeypatch.setitem(CONSTRAINT_REGISTRY, SOFT_CODE, _raise)
    rx = recommend_next_session(_healthy_state(), goal="Strength")
    assert rx.type == baseline.type and rx.duration_min == baseline.duration_min
    assert [e.name for e in rx.exercises] == [e.name for e in baseline.exercises]
    assert rx.why is not None and rx.why.validation is not None
    assert rx.why.validation.unevaluated_hard == []


def test_evaluated_hard_violation_override_is_not_built_back_out():
    """The pre-existing defect on the same path: a wrist-stress override on a Gymnastics day
    still prescribed push-ups / a shoulder press, and a 60-minute block target stretched the
    35-minute recovery. A safety replacement is final."""
    s = _healthy_state()
    s.tissue_t.wrist = 80.0
    rx = recommend_next_session(s, goal="Gymnastics", block_context=BUILD_OUT)
    assert rx.type == "Recovery"
    assert rx.why is not None and rx.why.validation is not None
    assert rx.why.validation.hard_violations
    assert rx.exercises == []
    assert rx.duration_min == 35
    assert rx.why.expected_outcomes == []


# ── persisted, then re-evaluated (DB) ─────────────────────────────────────────────────


async def _latest_decision(db, user_id: int):
    from sqlalchemy import select

    from app.models.telemetry import PrescriptionDecision

    result = await db.execute(
        select(PrescriptionDecision)
        .where(PrescriptionDecision.athlete_id == user_id)
        .order_by(PrescriptionDecision.id.desc())
        .limit(1)
    )
    decision = result.scalars().first()
    assert decision is not None
    await db.refresh(decision)
    return decision


async def _today_content(client, db, session: PlannedSession) -> dict:
    resp = await client.get("/v1/planning/today", params={"goal": "Strength"})
    assert resp.status_code == 200, resp.text
    await db.refresh(session)
    assert session.prescribed_content is not None
    assert resp.json()["prescription"] == session.prescribed_content
    return session.prescribed_content


@pytest.mark.asyncio
async def test_rest_is_persisted_then_reevaluated_once_the_rule_runs(async_db, monkeypatch):
    user = User(email="w1c-rest@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=8, sessions_per_week=3,
        start_date=date.today(), deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)
    session = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=date.today(), week_number=1,
        day_of_week=date.today().isoweekday(), category="Heavy Lower", modality="Strength",
    )
    async_db.add(session)
    await async_db.commit()
    await async_db.refresh(session)

    async def _override_db():
        yield async_db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. The rule crashes → complete rest is what gets PERSISTED, not just returned.
            monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
            rest = await _today_content(client, async_db, session)
            assert rest["type"] == "Rest" and rest["duration_min"] == 0
            assert rest["exercises"] == [] and rest["structure"] is None
            assert rest["why"]["validation"]["unevaluated_hard"] == [HARD_CODE]
            decision = await _latest_decision(async_db, user.id)
            assert decision.final_outcome == "safety_unevaluated_rest"
            assert decision.final_prescription_type == "Rest" and decision.final_duration_min == 0
            assert decision.unevaluated_hard_json == [HARD_CODE]
            # The ranking evidence is kept: the ranked winner is still recorded, not erased.
            assert decision.chosen_candidate_id is not None

            # 2. The rule is fixed → the next read re-evaluates and replaces the rest.
            monkeypatch.undo()
            after = await _today_content(client, async_db, session)
            assert after["type"] != "Rest" and after["duration_min"] > 0
            assert after["why"]["validation"]["unevaluated_hard"] == []
            decision = await _latest_decision(async_db, user.id)
            assert decision.final_outcome == "as_ranked"
            assert decision.unevaluated_hard_json == []
    finally:
        app.dependency_overrides.clear()


# ── review follow-ups ─────────────────────────────────────────────────────────────────

PLANNED_UPPER = {"session_domain": "hypertrophy", "session_category": "High Volume Upper"}


def _returns_none(*_args: object) -> None:
    return None


def _truthy_non_bool(*_args: object) -> ConstraintResult:
    from app.logic.constraint_engine.types import Severity

    return ConstraintResult(passed="yes", severity=Severity.HARD, code=HARD_CODE)  # type: ignore[arg-type]


@pytest.mark.parametrize("rule", [_returns_none, _truthy_non_bool], ids=["none", "truthy-non-bool"])
def test_malformed_hard_rule_result_is_unevaluated(monkeypatch, rule):
    """A rule that returns `None` (AttributeError on `.passed`) or a truthy non-bool `passed`
    (which `if result.passed` waved through) has not evaluated the session."""
    monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, rule)
    report = SessionValidator(get_structured_template_for_goal("Running")).validate(
        {"type": "Endurance", "duration_min": 40}, build_constraint_context(_healthy_state(), None, "Running")
    )
    assert report.unevaluated_hard == [HARD_CODE]
    rx = recommend_next_session(_healthy_state(), goal="Strength")
    assert _complete_rest_problems(rx, HARD_CODE) == []


@pytest.mark.parametrize("rule", [_returns_none, _truthy_non_bool], ids=["none", "truthy-non-bool"])
def test_malformed_soft_rule_result_is_skipped(monkeypatch, rule):
    monkeypatch.setitem(CONSTRAINT_REGISTRY, SOFT_CODE, rule)
    report = SessionValidator(get_structured_template_for_goal("Running")).validate(
        {"type": "Endurance", "duration_min": 40}, build_constraint_context(_healthy_state(), None, "Running")
    )
    assert SOFT_CODE in report.skipped_codes and report.unevaluated_hard == []


def test_rest_never_claims_the_planned_session_was_followed(monkeypatch):
    """Finalize keeps the replaced candidate's branch id; that alone used to emit
    `plan:session_followed=hyp_upper_split` on a zero-minute Rest."""
    baseline = recommend_next_session(_healthy_state(), goal="Hypertrophy", block_context=PLANNED_UPPER)
    assert baseline.why is not None
    assert any(c.startswith("plan:session_followed=") for c in baseline.why.constraints_applied), (
        "fixture must bind a planned session the engine would otherwise follow"
    )
    monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
    rx = recommend_next_session(_healthy_state(), goal="Hypertrophy", block_context=PLANNED_UPPER)
    assert rx.why is not None
    codes = rx.why.constraints_applied
    assert not any(c.startswith("plan:session_followed=") for c in codes)
    assert "plan:session_replaced=hypertrophy_upper(safety_unevaluated)" in codes


def test_every_code_on_a_rest_prescription_has_a_real_label(monkeypatch):
    from app.logic.constraint_labels import UNKNOWN_LABEL, describe_constraints

    monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
    rx = recommend_next_session(_healthy_state(), goal="Hypertrophy", block_context=PLANNED_UPPER)
    assert rx.why is not None
    labels = {e.code: e.label for e in describe_constraints(rx.why.constraints_applied)}
    unknown = sorted(code for code, label in labels.items() if label == UNKNOWN_LABEL)
    assert unknown == []
    assert "Rest today" in labels[f"safety:unevaluated={HARD_CODE}"]


def _ranked_winner(log: list) -> str | None:
    """What decision telemetry passes as the ranked winner: ``candidate_log[0]``."""
    return log[0].branch_id if log else None


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("unevaluated", "safety_unevaluated_rest"),
        ("wrist", "hard_violation_replaced"),
        ("plain", "as_ranked"),
    ],
)
def test_final_outcome_is_recorded_apart_from_the_ranking(monkeypatch, setup, expected):
    from app.services.decision_telemetry import final_outcome

    state, goal = _healthy_state(), "Hypertrophy"
    if setup == "unevaluated":
        monkeypatch.setitem(CONSTRAINT_REGISTRY, HARD_CODE, _raise)
    if setup == "wrist":
        state.tissue_t.wrist = 80.0
        goal = "Gymnastics"
    log: list = []
    rx = recommend_next_session(state, goal=goal, candidate_log_out=log)
    assert final_outcome(rx, _ranked_winner(log)) == expected


def _unranked_exit(path: str):
    """Drive one real early exit and return (prescription, candidate_log) exactly as the
    service hands them to decision telemetry."""
    log: list = []
    if path == "equipment_unavailable":
        from app.scripts import simulate_matrix as sm

        level_key, _ = sm.EXPERIENCE["intermediate"]
        rx = recommend_next_session(
            sm._state(level_key, *sm.FRESHNESS["fresh"]),
            goal="OlympicLifts",  # type: ignore[arg-type]
            catalog=sm._catalog(),
            available_equipment=["bodyweight"],
            block_context={
                "block_goal": "OlympicLifts", "session_category": "Weightlifting Technique",
                "session_domain": "weightlifting", "week_number": 2, "duration_weeks": 8,
                "deload_every_n_weeks": 4,
            },
            candidate_log_out=log,
        )
    else:
        from app.logic.planning_constraints import (
            AuthorityClass,
            ConstraintKind,
            ConstraintScope,
            Hardness,
            ResolvedPlanningConstraint,
        )

        bar_everything = ResolvedPlanningConstraint(
            kind=ConstraintKind.MAX_DURATION_MIN, hardness=Hardness.HARD,
            authority_class=AuthorityClass.USER_OVERRIDE, scope=ConstraintScope.MICROCYCLE,
            reason_code="athlete_excluded", source_type="synthetic_external", threshold=0.0,
        )
        rx = recommend_next_session(
            _healthy_state(), goal="Running", constraints=[bar_everything], candidate_log_out=log
        )
    return rx, log


@pytest.mark.parametrize("path", ["equipment_unavailable", "constraint_infeasible"])
def test_unranked_exits_get_their_own_outcome_never_as_ranked(path):
    """These exits prescribe without ranking a pool to choose from. `as_ranked` ("the ranked
    winner was prescribed") was the fall-through default and mislabelled both."""
    from app.services.decision_telemetry import final_outcome

    rx, log = _unranked_exit(path)
    assert rx.why is not None and rx.why.prescription_branch == path, "fixture must hit the exit"
    assert final_outcome(rx, _ranked_winner(log)) == path


def test_an_unrecognised_unranked_prescription_is_unknown_not_as_ranked():
    from app.schemas.prescription import PrescriptionExplanation
    from app.services.decision_telemetry import final_outcome

    rx = recommend_next_session(_healthy_state(), goal="Strength")
    assert rx.why is not None
    orphan = rx.model_copy(
        update={"why": PrescriptionExplanation(**{**rx.why.model_dump(), "prescription_branch": "some_new_exit"})}
    )
    assert final_outcome(orphan, None) == "unknown"
    assert final_outcome(orphan, "a_different_winner") == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["equipment_unavailable", "constraint_infeasible"])
async def test_unranked_exit_decision_row(async_db, path):
    """The persisted decision row for a real no-candidate exit (the writer, not just the helper)."""
    from sqlalchemy import select

    from app.models.telemetry import PrescriptionDecision
    from app.services.decision_telemetry import persist_prescription_decision

    user = User(email=f"w1c-{path}@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    rx, log = _unranked_exit(path)
    await persist_prescription_decision(async_db, user.id, rx, log, goal="x")
    row = (
        await async_db.execute(
            select(PrescriptionDecision).where(PrescriptionDecision.athlete_id == user.id)
        )
    ).scalars().one()
    assert row.final_outcome == path
    assert row.final_duration_min == rx.duration_min


def test_projection_treats_stored_rest_as_zero_work():
    """A stored Rest (duration 0) used to project as the block's 60-minute target workout,
    with positive fatigue and adaptation dose, under basis="prescribed"."""
    from test_planning_projection_service import D0, _block, _session, _state

    from app.services.planning_projection_service import project_planned_days

    block = _block(target_session_minutes=60, goal=BlockGoal.HYPERTROPHY)
    rest_content = {"type": "Rest", "duration_min": 0, "exercises": [], "structure": None}
    rest, _ = project_planned_days(
        _state(), D0, D0, {D0: [(_session(1, D0, prescribed_content=rest_content), block)]}
    )
    empty, _ = project_planned_days(_state(), D0, D0, {})
    trained, _ = project_planned_days(_state(), D0, D0, {D0: [(_session(1, D0), block)]})

    row = rest[0].sessions[0]
    assert (row.modality, row.basis, row.load) == ("Rest", "prescribed", 0.0)
    assert rest[0].load == 0.0
    # Zero work: the day ends exactly as a day with no session, and below a trained day.
    assert rest[0].fatigue == empty[0].fatigue
    assert rest[0].mean_fatigue < trained[0].mean_fatigue


def test_planned_winner_rejected_by_a_hard_rule_is_not_reported_as_followed():
    """The evaluated-violation twin of the rest case: the replaced winner keeps its branch id
    (`gym_skill`, which the planned Gymnastics Skill slot binds), and that alone used to read
    as `plan:session_followed=gym_skill` on a recovery override."""
    s = _healthy_state()
    s.tissue_t.wrist = 80.0
    rx = recommend_next_session(
        s, goal="Gymnastics",
        block_context={"session_domain": "gymnastics", "session_category": "Gymnastics Skill"},
    )
    assert rx.why is not None and rx.why.validation is not None
    assert rx.why.validation.hard_violations
    plan_codes = [c for c in rx.why.constraints_applied if c.startswith("plan:")]
    assert plan_codes == ["plan:session_replaced=gymnastics_skill(validation)"]


@pytest.mark.parametrize("position", ["before", "between", "after"])
def test_a_rest_row_never_moves_a_same_day_workout(position):
    """Same-day sessions are placed an hour apart. Rest rows used to take a slot in that
    count, so a rest BEFORE a workout pushed it from 12:00 to 13:00 and changed the forecast
    fatigue (5.82 -> 5.91 in review). Rest is zero work and takes no slot."""
    from test_planning_projection_service import D0, _block, _session, _state

    from app.services.planning_projection_service import project_planned_days

    block = _block(target_session_minutes=60, goal=BlockGoal.HYPERTROPHY)
    rest = _session(9, D0, prescribed_content={"type": "Rest", "duration_min": 0})
    w1, w2 = _session(1, D0), _session(2, D0, category="Heavy Upper")
    with_rest = {
        "before": [rest, w1, w2],
        "between": [w1, rest, w2],
        "after": [w1, w2, rest],
    }[position]
    plain, _ = project_planned_days(_state(), D0, D0, {D0: [(w1, block), (w2, block)]})
    mixed, _ = project_planned_days(_state(), D0, D0, {D0: [(s, block) for s in with_rest]})
    assert mixed[0].load == plain[0].load
    assert mixed[0].fatigue == plain[0].fatigue
    assert mixed[0].mean_fatigue == plain[0].mean_fatigue
