"""INT-A18: POST /v1/log-workout must not persist another athlete's planned_session_id.

``planned_session_id`` on the ingest payload is caller-supplied. The ownership
lookup (``_match_planned_session``) is scoped to the caller, so a foreign id
simply fails to match — and the raw id was still copied onto the persisted
``WorkoutLog`` row, leaving it pointing at the victim's session (FK pollution).

Mirrors the two-user IDOR pattern in tests/test_session_feedback_routes.py.
"""
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.workout_log import WorkoutLog

pytestmark = pytest.mark.asyncio

_WORKOUT_BODY = {
    "modality": "Strength",
    "duration_minutes": 60.0,
    "session_rpe": 7.5,
    "total_volume_load": 4000.0,
    "estimated_sets": 12,
    "sleep_quality": 7.0,
    "life_stress_inverse": 7.0,
}


async def _register_and_get_token(client, email: str, password: str = "securepass1") -> str:
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


def _body(**overrides) -> dict:
    return dict(_WORKOUT_BODY, timestamp=datetime.now(UTC).isoformat(), **overrides)


async def test_log_workout_rejects_other_users_planned_session(http_client, async_db):
    """Attacker's log referencing the victim's planned session must be refused."""
    owner_tok = await _register_and_get_token(http_client, "ps_owner@test.com")
    owner_hdr = {"Authorization": f"Bearer {owner_tok}"}
    owner_id = await _current_user_id(http_client, owner_hdr)
    ps = await _mk_planned_session(async_db, owner_id)

    atk_tok = await _register_and_get_token(http_client, "ps_attacker@test.com")
    atk_hdr = {"Authorization": f"Bearer {atk_tok}"}
    atk_id = await _current_user_id(http_client, atk_hdr)

    resp = await http_client.post(
        "/v1/log-workout", json=_body(planned_session_id=ps.id), headers=atk_hdr
    )
    assert resp.status_code == 404, resp.text

    # The attacker's log must not exist holding the victim's session id.
    rows = (
        await async_db.execute(select(WorkoutLog).where(WorkoutLog.user_id == atk_id))
    ).scalars().all()
    assert [r.planned_session_id for r in rows] == [], (
        "attacker persisted a workout log referencing another athlete's planned session"
    )

    # And the victim's session is untouched.
    await async_db.refresh(ps)
    assert ps.status == SessionStatus.PENDING
    assert ps.workout_log_id is None


async def test_log_workout_rejects_nonexistent_planned_session(http_client, async_db):
    tok = await _register_and_get_token(http_client, "ps_ghost@test.com")
    hdr = {"Authorization": f"Bearer {tok}"}
    user_id = await _current_user_id(http_client, hdr)

    resp = await http_client.post(
        "/v1/log-workout", json=_body(planned_session_id=987654321), headers=hdr
    )
    assert resp.status_code == 404, resp.text

    rows = (
        await async_db.execute(select(WorkoutLog).where(WorkoutLog.user_id == user_id))
    ).scalars().all()
    assert rows == []


async def test_log_workout_accepts_own_planned_session(http_client, async_db):
    """Regression guard: the legitimate linkage path still completes the session."""
    tok = await _register_and_get_token(http_client, "ps_legit@test.com")
    hdr = {"Authorization": f"Bearer {tok}"}
    user_id = await _current_user_id(http_client, hdr)
    ps = await _mk_planned_session(async_db, user_id)

    resp = await http_client.post(
        "/v1/log-workout", json=_body(planned_session_id=ps.id), headers=hdr
    )
    assert resp.status_code == 200, resp.text

    row = (
        await async_db.execute(select(WorkoutLog).where(WorkoutLog.user_id == user_id))
    ).scalars().first()
    assert row is not None
    assert row.planned_session_id == ps.id

    await async_db.refresh(ps)
    assert ps.status == SessionStatus.COMPLETED
    assert ps.workout_log_id == row.id
