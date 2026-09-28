"""GET /v1/planning/projection — the C1b planned-week projection route (ADR-0073)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.athlete_state import AthleteState
from app.services import state_service
from app.services.planning_projection_service import MAX_WINDOW_DAYS

pytestmark = pytest.mark.asyncio

URL = "/v1/planning/projection"


async def _register(client, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "pw123456"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "pw123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    me = await client.get("/auth/me", headers=hdr)
    return hdr, me.json()["id"]


async def _state_rows(db, uid: int) -> int:
    res = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == uid)
    )
    return int(res.scalar_one())


async def test_requires_auth(http_client) -> None:
    resp = await http_client.get(URL)
    assert resp.status_code == 401


async def test_no_state_is_200_unavailable(http_client, async_db) -> None:
    hdr, uid = await _register(http_client, "c1b-route-nostate@t.io")

    resp = await http_client.get(URL, headers=hdr)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["available"], body["reason"], body["days"]) == (False, "no_state", [])
    assert await _state_rows(async_db, uid) == 0  # never initializes


async def test_state_invalid_is_200_unavailable_not_409(http_client, async_db) -> None:
    hdr, uid = await _register(http_client, "c1b-route-invalid@t.io")
    async_db.add(
        AthleteState(
            user_id=uid,
            timestamp=datetime(2026, 9, 1, 12, 0),
            c_met_aerobic=300.0,
            c_nm_force=1400.0,
            c_struct=100.0,
            b_met_anaerobic=15000.0,
            f_met_systemic=5.0,
            f_nm_peripheral=5.0,
            f_nm_central=5.0,
            f_struct_damage=5.0,
            s_struct_signal=0.0,
            habit_strength=0.5,
            skill_state={},
            engine_state={"version": 2, "x": {}, "f": {}, "t": {}},
        )
    )
    await async_db.commit()

    resp = await http_client.get(URL, headers=hdr)

    assert resp.status_code == 200, resp.text
    assert (resp.json()["available"], resp.json()["reason"]) == (False, "state_invalid")


async def test_shape_with_an_active_block(http_client, async_db) -> None:
    hdr, uid = await _register(http_client, "c1b-route-shape@t.io")
    await state_service.initialize_athlete_state(async_db, uid)
    today = date.today()
    created = await http_client.post(
        "/v1/planning/blocks",
        headers=hdr,
        json={"goal": "Strength", "start_date": today.isoformat(), "duration_weeks": 2,
              "sessions_per_week": 7},
    )
    assert created.status_code == 200, created.text
    before = await _state_rows(async_db, uid)

    resp = await http_client.get(URL, headers=hdr)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True and body["reason"] is None
    window = body["window"]
    assert window["block_id"] == created.json()["id"] and window["week_number"] == 1
    assert window["start"] == today.isoformat()
    assert window["end"] == (today + timedelta(days=6)).isoformat()
    assert len(body["days"]) == 7
    day = body["days"][0]
    assert set(day) == {"date", "sessions", "load", "mean_fatigue", "fatigue"}
    assert set(day["fatigue"]) == {"cns", "muscular", "metabolic", "structural", "tendon", "grip"}
    planned = [d for d in body["days"] if d["sessions"]]
    assert planned, "a 7-session block week projects sessions"
    s = planned[0]["sessions"][0]
    assert set(s) == {"planned_session_id", "modality", "basis", "load"}
    assert s["basis"] == "template_estimate" and s["load"] > 0
    assert body["peak_mean_fatigue"] == max(d["mean_fatigue"] for d in body["days"])
    assert await _state_rows(async_db, uid) == before  # read-only


async def test_through_is_capped_and_past_through_is_422(http_client, async_db) -> None:
    hdr, uid = await _register(http_client, "c1b-route-cap@t.io")
    await state_service.initialize_athlete_state(async_db, uid)
    today = date.today()

    far = await http_client.get(
        URL, headers=hdr, params={"through": (today + timedelta(days=200)).isoformat()}
    )
    assert far.status_code == 200, far.text
    assert far.json()["window"]["end"] == (today + timedelta(days=MAX_WINDOW_DAYS - 1)).isoformat()
    assert len(far.json()["days"]) == MAX_WINDOW_DAYS

    past = await http_client.get(
        URL, headers=hdr, params={"through": (today - timedelta(days=1)).isoformat()}
    )
    assert past.status_code == 422
