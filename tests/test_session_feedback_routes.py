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


async def _mk_planned_session(async_db, user_id: int) -> PlannedSession:
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
        day_of_week=1,
        category="Heavy Lower",
        modality="strength",
        status=SessionStatus.PENDING,
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
