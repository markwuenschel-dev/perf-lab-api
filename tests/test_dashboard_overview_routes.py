"""Route contract test for GET /v1/dashboard/overview (requires a live DB).

Registers a user, logs workouts spanning the chronic window, creates a block
with completed/skipped planned sessions, then asserts sensible ACWR + adherence
values. A brand-new user (no history) must degrade to nulls/insufficient, never
500. Mirrors the http_client + real-auth-flow pattern in the other route tests.
"""
from datetime import date, datetime, timedelta

import pytest

from app.models.mesocycle import (
    BlockGoal,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
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


async def _user_id(client, hdr) -> int:
    me = await client.get("/auth/me", headers=hdr)
    assert me.status_code == 200, me.text
    return me.json()["id"]


async def test_overview_empty_history_is_insufficient_not_500(http_client):
    token = await _register_and_get_token(http_client, "ov_empty@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/dashboard/overview", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["training_load"]["status"] == "insufficient"
    assert body["training_load"]["acwr"] is None
    assert body["training_load"]["sweet_spot_low"] == 0.8
    assert body["training_load"]["sweet_spot_high"] == 1.3

    assert body["adherence"]["pct"] is None
    assert body["adherence"]["streak_days"] == 0
    assert body["adherence"]["window_days"] == 28


async def test_overview_with_history_reports_real_values(http_client, async_db):
    token = await _register_and_get_token(http_client, "ov_full@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    uid = await _user_id(http_client, hdr)

    today = date.today()

    # Steady workouts across the last 28 days (span > MIN_HISTORY_DAYS) so a
    # chronic baseline exists; a couple in the last 7 days for the acute leg.
    for offset in (0, 2, 5, 9, 14, 20, 27):
        ts = datetime.combine(today - timedelta(days=offset), datetime.min.time())
        async_db.add(
            WorkoutLog(
                user_id=uid,
                session_timestamp=ts,
                modality="Strength",
                duration_minutes=60.0,
                session_rpe=8.0,
            )
        )

    # A block with planned sessions in the adherence window: 3 completed, 1 skipped.
    block = MesocycleBlock(
        user_id=uid,
        goal=BlockGoal.GENERAL,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=today - timedelta(days=27),
    )
    async_db.add(block)
    await async_db.flush()

    plan = [
        (0, SessionStatus.COMPLETED),
        (2, SessionStatus.COMPLETED),
        (5, SessionStatus.COMPLETED),
        (9, SessionStatus.SKIPPED),
    ]
    for offset, status in plan:
        async_db.add(
            PlannedSession(
                block_id=block.id,
                user_id=uid,
                scheduled_date=today - timedelta(days=offset),
                week_number=1,
                day_of_week=1,
                category="Heavy Lower",
                modality="Strength",
                status=status,
            )
        )
    await async_db.commit()

    resp = await http_client.get("/v1/dashboard/overview", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    tl = body["training_load"]
    assert tl["status"] in {"low", "optimal", "high"}
    assert tl["acwr"] is not None
    assert tl["acute"] is not None
    assert tl["chronic"] is not None

    adh = body["adherence"]
    # 3 completed of 4 scheduled = 75%.
    assert adh["pct"] == 75.0
    # Completed sessions / workouts today, day-2 and day-5 → streak resumes today.
    assert adh["streak_days"] >= 1
