"""The plan the athlete was shown is the session they are prescribed — or they are told why not.

Before this, a block's day ("High Volume Upper") and a template's type ("Upper Body Hypertrophy")
came from two vocabularies joined only by an exact-string boost that could never match, so the
plan had no effect on selection at all. These tests pin the join, and pin the things that still
outrank the plan: readiness redirects, safety overrides and hard constraints.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_prescriber_candidates import _state

from app.logic.prescriber import recommend_next_session
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.services.state_service import initialize_athlete_state

#: ``TrainingGoal`` is a Literal alias, not an enum — the goal travels as its plain string.
HYPERTROPHY = "Hypertrophy"

UPPER_DAY = {"block_goal": "Hypertrophy", "session_category": "High Volume Upper"}
LOWER_DAY = {"block_goal": "Hypertrophy", "session_category": "High Volume Lower"}


def _codes(rx) -> list[str]:
    return list(rx.why.constraints_applied) if rx.why is not None else []


def _plan_codes(rx) -> list[str]:
    return [c for c in _codes(rx) if c.startswith("plan:session_")]


# ── the plan decides the session ────────────────────────────────────────────────

def test_the_planned_upper_day_is_the_session_prescribed() -> None:
    rx = recommend_next_session(
        _state(), goal=HYPERTROPHY, block_context=dict(UPPER_DAY)
    )
    assert rx.type == "Upper Body Hypertrophy", rx.type
    assert _plan_codes(rx) == ["plan:session_followed=hyp_upper_split"]


def test_the_planned_lower_day_is_a_different_session_from_the_same_block() -> None:
    """Same athlete, same goal, different planned day — the plan is what differs."""
    upper = recommend_next_session(
        _state(), goal=HYPERTROPHY, block_context=dict(UPPER_DAY)
    )
    lower = recommend_next_session(
        _state(), goal=HYPERTROPHY, block_context=dict(LOWER_DAY)
    )
    assert upper.type != lower.type
    assert lower.type == "High Volume Hypertrophy", lower.type
    assert _plan_codes(lower) == ["plan:session_followed=hyp_high_vol"]


def test_an_unbound_planned_day_changes_nothing_and_claims_nothing() -> None:
    """A category no binding covers must leave selection alone and stay silent."""
    free = recommend_next_session(_state(), goal=HYPERTROPHY)
    unbound = recommend_next_session(
        _state(),
        goal=HYPERTROPHY,
        block_context={"block_goal": "Hypertrophy", "session_category": "Heavy Lower"},
    )
    assert unbound.type == free.type
    assert _plan_codes(unbound) == []


# ── what still outranks the plan ────────────────────────────────────────────────

def test_a_safety_override_beats_the_plan_and_says_so() -> None:
    hurt = _state(lumbar=85.0, knee=85.0, f_struct_damage=80.0)
    rx = recommend_next_session(hurt, goal=HYPERTROPHY, block_context=dict(UPPER_DAY))
    assert rx.type != "Upper Body Hypertrophy"
    assert _plan_codes(rx) == ["plan:session_replaced=hypertrophy_upper(safety)"]
    assert any(c.startswith("safety:override=") for c in _codes(rx))


def test_a_readiness_redirect_beats_the_plan_and_says_so() -> None:
    """Redirects exist to pull work down on a bad day; the plan must not talk over them."""
    fatigued = _state(muscular=88.0, cns=70.0)
    rx = recommend_next_session(
        fatigued, goal=HYPERTROPHY, block_context=dict(UPPER_DAY)
    )
    assert rx.type != "Upper Body Hypertrophy"
    assert _plan_codes(rx) == ["plan:session_replaced=hypertrophy_upper(readiness)"]


def test_an_ineligible_planned_template_falls_back_rather_than_forcing_it() -> None:
    """`hyp_upper_split` is gated on muscular fatigue; when it is out, the plan cannot be met."""
    from app.logic.candidate_library import HYPERTROPHY_TEMPLATES

    upper = next(t for t in HYPERTROPHY_TEMPLATES if t.branch_id == "hyp_upper_split")
    assert upper.state_eligible is not None, "this test assumes the upper split is state-gated"
    sore = _state(muscular=60.0)
    assert not upper.state_eligible(sore), "fixture no longer makes the planned template ineligible"

    rx = recommend_next_session(sore, goal=HYPERTROPHY, block_context=dict(UPPER_DAY))
    assert rx.type != "Upper Body Hypertrophy"
    assert _plan_codes(rx) and _plan_codes(rx)[0].startswith("plan:session_replaced=hypertrophy_upper(")


# ── through the real route ──────────────────────────────────────────────────────

pytestmark_asyncio = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_planning_today_prescribes_the_planned_session(http_client, async_db):
    """The seam that matters: a real block's planned day reaches the prescription over HTTP."""
    user = User(email="plan-binding-route@test.com", hashed_password="x", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell", "machine"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    block = MesocycleBlock(
        user_id=user.id,
        goal=BlockGoal.HYPERTROPHY,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=date.today() - timedelta(days=1),
        deload_every_n_weeks=4,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)
    async_db.add(
        PlannedSession(
            block_id=block.id,
            user_id=user.id,
            scheduled_date=date.today(),
            week_number=1,
            day_of_week=date.today().isoweekday(),
            category="High Volume Upper",
            modality="Hypertrophy",
        )
    )
    await async_db.commit()

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = await http_client.get("/v1/planning/today", params={"goal": "Hypertrophy"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    rx = payload["prescription"]
    assert rx is not None, payload
    assert rx["type"] == "Upper Body Hypertrophy", rx["type"]
    entries = [e for e in rx["why"]["constraint_details"] if e["code"].startswith("plan:session_")]
    assert [e["code"] for e in entries] == ["plan:session_followed=hyp_upper_split"]
    (entry,) = entries
    assert entry["athlete_visible"] is True and entry["group"] == "plan_rule"
    assert entry["label"] == "This is the session your plan plans for today."
