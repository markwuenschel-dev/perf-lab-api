"""Route contract tests for POST /v1/feedback (requires a live DB).

Covers the happy path (persist an athlete-reported row), the no-IDOR guard
(cannot file feedback against another user's planned session or workout log),
the one-per-session uniqueness (409), and auth. Mirrors the http_client +
real-auth-flow pattern in tests/test_objectives_routes.py.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.telemetry import SessionFeedback
from app.models.workout_log import WorkoutLog

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client, email: str, password: str) -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def _current_user_id(client, hdr) -> int:
    me = await client.get("/auth/me", headers=hdr)
    assert me.status_code == 200, me.text
    return me.json()["id"]


async def _mk_planned_session(
    async_db, user_id: int, *, status: SessionStatus = SessionStatus.COMPLETED
) -> PlannedSession:
    """A planned session for the given user.

    Defaults to COMPLETED because feedback describes an outcome and is refused for
    a session that has not reached one (ADR-0070). Pass PENDING to exercise that
    refusal.
    """
    block = MesocycleBlock(
        user_id=user_id,
        goal=BlockGoal.STRENGTH,
        status=BlockStatus.ACTIVE,
        duration_weeks=1,
        start_date=date.today(),
        weekly_template=[],
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)

    ps = PlannedSession(
        block_id=block.id,
        user_id=user_id,
        scheduled_date=date.today(),
        week_number=1,
        block_day=1,
        day_of_week=date.today().isoweekday(),
        category="Heavy Lower",
        modality="strength",
        status=status,
    )
    async_db.add(ps)
    await async_db.commit()
    await async_db.refresh(ps)
    return ps


async def _mk_workout_log(async_db, user_id: int) -> WorkoutLog:
    wl = WorkoutLog(
        user_id=user_id,
        session_timestamp=datetime.utcnow(),
        modality="strength",
        duration_minutes=60.0,
        session_rpe=7.0,
    )
    async_db.add(wl)
    await async_db.commit()
    await async_db.refresh(wl)
    return wl


async def test_create_feedback_persists_reported_fields(http_client, async_db):
    token = await _register_and_get_token(http_client, "fb_happy@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    user_id = await _current_user_id(http_client, hdr)

    ps = await _mk_planned_session(async_db, user_id)
    wl = await _mk_workout_log(async_db, user_id)
    # Mirror what logging actually does: `state_service.process_new_workout` links
    # the log to the session it fulfilled. Feedback may only reference that link.
    ps.workout_log_id = wl.id
    await async_db.commit()

    resp = await http_client.post(
        "/v1/feedback",
        json={
            "planned_session_id": ps.id,
            "completed_workout_log_id": wl.id,
            "status": "modified",
            "followed_as_prescribed": False,
            "modified_volume": True,
            "modification_reason": "cut last set, tight hip",
            "satisfaction_score": 4,
            "perceived_fit_score": 3,
            "soreness_flag": True,
        },
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["planned_session_id"] == ps.id
    assert body["completed_workout_log_id"] == wl.id
    assert body["status"] == "modified"
    assert body["followed_as_prescribed"] is False
    assert body["modified_volume"] is True
    assert body["satisfaction_score"] == 4
    assert body["soreness_flag"] is True

    # Confirm the row actually persisted with the reported fields.
    row = (
        await async_db.execute(
            select(SessionFeedback).where(SessionFeedback.planned_session_id == ps.id)
        )
    ).scalars().first()
    assert row is not None
    assert row.status == "modified"
    assert row.followed_as_prescribed is False
    assert row.modified_volume is True
    assert row.satisfaction_score == 4


async def test_create_feedback_rejects_other_users_session(http_client, async_db):
    # Owner creates a session; attacker (different user) tries to file feedback on it.
    owner_tok = await _register_and_get_token(http_client, "fb_owner@test.com", "securepass1")
    owner_hdr = {"Authorization": f"Bearer {owner_tok}"}
    owner_id = await _current_user_id(http_client, owner_hdr)
    ps = await _mk_planned_session(async_db, owner_id)

    atk_tok = await _register_and_get_token(http_client, "fb_attacker@test.com", "securepass1")
    atk_hdr = {"Authorization": f"Bearer {atk_tok}"}

    resp = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": ps.id, "status": "completed"},
        headers=atk_hdr,
    )
    assert resp.status_code == 404, resp.text

    # And nothing was written.
    row = (
        await async_db.execute(
            select(SessionFeedback).where(SessionFeedback.planned_session_id == ps.id)
        )
    ).scalars().first()
    assert row is None


async def test_create_feedback_rejects_other_users_workout_log(http_client, async_db):
    owner_tok = await _register_and_get_token(http_client, "fb_wl_owner@test.com", "securepass1")
    owner_hdr = {"Authorization": f"Bearer {owner_tok}"}
    owner_id = await _current_user_id(http_client, owner_hdr)
    ps = await _mk_planned_session(async_db, owner_id)

    other_tok = await _register_and_get_token(http_client, "fb_wl_other@test.com", "securepass1")
    other_hdr = {"Authorization": f"Bearer {other_tok}"}
    other_id = await _current_user_id(http_client, other_hdr)
    other_wl = await _mk_workout_log(async_db, other_id)

    # Owner's own session, but a workout log that belongs to someone else.
    resp = await http_client.post(
        "/v1/feedback",
        json={
            "planned_session_id": ps.id,
            "completed_workout_log_id": other_wl.id,
            "status": "completed",
        },
        headers=owner_hdr,
    )
    assert resp.status_code == 404, resp.text


async def test_create_feedback_is_one_per_session(http_client, async_db):
    token = await _register_and_get_token(http_client, "fb_dupe@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    user_id = await _current_user_id(http_client, hdr)
    ps = await _mk_planned_session(async_db, user_id)

    first = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": ps.id, "status": "completed"},
        headers=hdr,
    )
    assert first.status_code == 201, first.text

    dupe = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": ps.id, "status": "skipped"},
        headers=hdr,
    )
    assert dupe.status_code == 409, dupe.text


async def test_create_feedback_unauthenticated(http_client):
    resp = await http_client.post(
        "/v1/feedback", json={"planned_session_id": 1, "status": "completed"}
    )
    assert resp.status_code == 401


async def test_feedback_refused_for_a_session_that_has_not_happened(http_client, async_db):
    """A PENDING session has no outcome yet, so there is nothing to report.

    This is the boundary that keeps `PlannedSession.status` canonical (ADR-0070):
    without it an athlete could declare a skip through feedback while the session
    stayed PENDING, and the adherence aggregate would hold two disagreeing
    accounts of one session.
    """
    token = await _register_and_get_token(http_client, "fb_pending@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    uid = await _current_user_id(http_client, hdr)
    ps = await _mk_planned_session(async_db, uid, status=SessionStatus.PENDING)

    resp = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": ps.id, "status": "skipped"},
        headers=hdr,
    )
    assert resp.status_code == 409, resp.text

    # And nothing was written — a refused report must leave no trace.
    rows = (
        await async_db.execute(
            select(SessionFeedback).where(SessionFeedback.planned_session_id == ps.id)
        )
    ).scalars().all()
    assert rows == []


async def test_list_feedback_returns_only_the_callers_rows(http_client, async_db):
    """The read route is scoped through PlannedSession, which owns the user id."""
    mine = await _register_and_get_token(http_client, "fb_list_mine@test.com", "securepass1")
    mine_hdr = {"Authorization": f"Bearer {mine}"}
    mine_uid = await _current_user_id(http_client, mine_hdr)

    theirs = await _register_and_get_token(http_client, "fb_list_other@test.com", "securepass1")
    theirs_hdr = {"Authorization": f"Bearer {theirs}"}
    theirs_uid = await _current_user_id(http_client, theirs_hdr)

    my_ps = await _mk_planned_session(async_db, mine_uid)
    their_ps = await _mk_planned_session(async_db, theirs_uid)

    assert (
        await http_client.post(
            "/v1/feedback",
            json={"planned_session_id": my_ps.id, "status": "completed"},
            headers=mine_hdr,
        )
    ).status_code == 201
    assert (
        await http_client.post(
            "/v1/feedback",
            json={"planned_session_id": their_ps.id, "status": "completed"},
            headers=theirs_hdr,
        )
    ).status_code == 201

    listed = await http_client.get("/v1/feedback", headers=mine_hdr)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [r["planned_session_id"] for r in body] == [my_ps.id]


async def test_list_feedback_unauthenticated(http_client):
    assert (await http_client.get("/v1/feedback")).status_code == 401


async def test_a_workout_log_that_did_not_fulfill_this_session_is_refused(http_client, async_db):
    """Owning the log is not enough — it must be *this* session's log.

    Ownership alone would let an athlete attach any of their own workouts to any of
    their own sessions, which makes the link meaningless to anything that later
    trusts it.
    """
    token = await _register_and_get_token(http_client, "fb_wronglog@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    uid = await _current_user_id(http_client, hdr)

    ps = await _mk_planned_session(async_db, uid)
    unrelated = await _mk_workout_log(async_db, uid)  # the athlete's, but not this session's

    resp = await http_client.post(
        "/v1/feedback",
        json={
            "planned_session_id": ps.id,
            "completed_workout_log_id": unrelated.id,
            "status": "completed",
        },
        headers=hdr,
    )
    assert resp.status_code == 409, resp.text


async def test_feedback_may_not_contradict_what_the_session_says_happened(http_client, async_db):
    """`PlannedSession.status` owns occurrence, so a contradicting report is refused."""
    token = await _register_and_get_token(http_client, "fb_contradict@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    uid = await _current_user_id(http_client, hdr)

    completed = await _mk_planned_session(async_db, uid)
    resp = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": completed.id, "status": "skipped"},
        headers=hdr,
    )
    assert resp.status_code == 409, resp.text

    skipped = await _mk_planned_session(async_db, uid, status=SessionStatus.SKIPPED)
    resp = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": skipped.id, "status": "completed"},
        headers=hdr,
    )
    assert resp.status_code == 409, resp.text

    # "unknown" contradicts nothing, so it is accepted against either.
    ok = await http_client.post(
        "/v1/feedback",
        json={"planned_session_id": skipped.id, "status": "unknown"},
        headers=hdr,
    )
    assert ok.status_code == 201, ok.text
