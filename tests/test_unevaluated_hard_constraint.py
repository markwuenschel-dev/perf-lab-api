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
    assert final_outcome(recommend_next_session(state, goal=goal)) == expected


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
