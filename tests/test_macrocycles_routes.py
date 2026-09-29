"""Route contract tests for /v1/macrocycles (requires a live DB).

Create (anchored to an objective) → list → get (with computed "week X of Y") →
patch status → delete round trip, plus the no-IDOR anchor gate (can't anchor to
another user's objective). Mirrors the http_client + real-auth pattern in
tests/test_objectives_routes.py.
"""
from datetime import date, timedelta

import pytest

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


async def _create_objective(client, hdr, *, label: str, target_in_days: int | None = None) -> dict:
    body: dict = {"label": label, "priority": 1}
    if target_in_days is not None:
        body["target_date"] = (date.today() + timedelta(days=target_in_days)).isoformat()
    resp = await client.post("/v1/objectives", json=body, headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_macrocycle_create_list_get_patch_delete(http_client):
    token = await _register_and_get_token(http_client, "macro_main@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    objective = await _create_objective(http_client, hdr, label="Nationals", target_in_days=28)
    start = date.today().isoformat()

    create_resp = await http_client.post(
        "/v1/macrocycles",
        json={"objective_id": objective["id"], "start_date": start},
        headers=hdr,
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["objective_id"] == objective["id"]
    assert created["objective_label"] == "Nationals"
    assert created["block_count"] == 0
    # start today, target +28d → 4-week horizon, week 1, 25% elapsed.
    wp = created["week_progress"]
    assert wp["current_week"] == 1
    assert wp["total_weeks"] == 4
    assert wp["pct"] == 25.0
    assert wp["weeks_to_go"] == 4
    # No blocks yet: the whole horizon is unplanned (measured from the macrocycle start).
    assert created["blocks"] == []
    assert created["unplanned_weeks"] == 4

    macro_id = created["id"]
    listed = (await http_client.get("/v1/macrocycles", headers=hdr)).json()
    assert len(listed) == 1 and listed[0]["id"] == macro_id

    got = await http_client.get(f"/v1/macrocycles/{macro_id}", headers=hdr)
    assert got.status_code == 200
    assert got.json()["target_date"] == objective["target_date"]

    patched = await http_client.patch(
        f"/v1/macrocycles/{macro_id}", json={"status": "achieved"}, headers=hdr
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "achieved"
    # Active-by-default list drops it once achieved.
    assert (await http_client.get("/v1/macrocycles", headers=hdr)).json() == []
    achieved = (
        await http_client.get("/v1/macrocycles", params={"status": "achieved"}, headers=hdr)
    ).json()
    assert len(achieved) == 1

    assert (await http_client.delete(f"/v1/macrocycles/{macro_id}", headers=hdr)).status_code == 204
    assert (await http_client.get(f"/v1/macrocycles/{macro_id}", headers=hdr)).status_code == 404


async def test_open_horizon_when_objective_has_no_target(http_client):
    token = await _register_and_get_token(http_client, "macro_open@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    objective = await _create_objective(http_client, hdr, label="Someday PR")  # no target_date

    created = (
        await http_client.post(
            "/v1/macrocycles", json={"objective_id": objective["id"]}, headers=hdr
        )
    ).json()
    wp = created["week_progress"]
    assert wp["current_week"] == 1
    assert wp["total_weeks"] is None
    assert wp["pct"] is None
    assert created["target_date"] is None
    assert created["unplanned_weeks"] is None  # no target date → no horizon to measure

    # Still null once a block exists: blocks are never persisted ahead (ADR-0040).
    block = await http_client.post(
        "/v1/planning/blocks",
        json={"goal": "Strength", "start_date": date.today().isoformat(), "duration_weeks": 2},
        headers=hdr,
    )
    assert block.status_code == 200, block.text
    got = (await http_client.get(f"/v1/macrocycles/{created['id']}", headers=hdr)).json()
    assert got["block_count"] == 1 and len(got["blocks"]) == 1
    assert got["unplanned_weeks"] is None


async def test_macrocycle_blocks_timeline(http_client):
    token = await _register_and_get_token(http_client, "macro_blocks@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    objective = await _create_objective(http_client, hdr, label="Worlds", target_in_days=70)
    today = date.today()
    macro = (
        await http_client.post(
            "/v1/macrocycles",
            json={"objective_id": objective["id"], "start_date": (today - timedelta(days=28)).isoformat()},
            headers=hdr,
        )
    ).json()

    async def _block(start: date, weeks: int, **extra) -> int:
        body = {"goal": "Strength", "start_date": start.isoformat(), "duration_weeks": weeks, **extra}
        resp = await http_client.post("/v1/planning/blocks", json=body, headers=hdr)
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    # Created out of date order on purpose: the timeline orders by start_date, id.
    upcoming_id = await _block(today + timedelta(days=28), 2)
    completed_id = await _block(today - timedelta(days=28), 2)
    current_id = await _block(
        today, 4, deload_every_n_weeks=4, benchmark_every_n_weeks=2
    )

    got = (await http_client.get(f"/v1/macrocycles/{macro['id']}", headers=hdr)).json()
    blocks = got["blocks"]
    assert [b["id"] for b in blocks] == [completed_id, current_id, upcoming_id]
    assert got["block_count"] == 3
    assert [b["phase"] for b in blocks] == ["completed", "current", "upcoming"]

    completed, current, upcoming = blocks
    assert completed["start_date"] == (today - timedelta(days=28)).isoformat()
    assert completed["end_date"] == (today - timedelta(days=15)).isoformat()  # weeks*7 - 1
    assert current["goal"] == "Strength" and current["status"] == "active"
    assert current["duration_weeks"] == 4
    assert current["deload_weeks"] == [4]
    assert current["benchmark_weeks"] == [2, 4]
    assert current["block_taper_week"] == 4  # final week of a 3+ week block
    # A 2-week block has no block-local taper, no deload (every 4) and no benchmark (every 4).
    assert upcoming["block_taper_week"] is None
    assert upcoming["deload_weeks"] == [] and upcoming["benchmark_weeks"] == []

    # Last block ends today+41; target today+70 → 28 uncovered days → 4 weeks.
    assert got["unplanned_weeks"] == 4

    listed = (await http_client.get("/v1/macrocycles", headers=hdr)).json()
    assert [b["id"] for b in listed[0]["blocks"]] == [completed_id, current_id, upcoming_id]


async def test_cannot_anchor_to_another_users_objective(http_client):
    tok_a = await _register_and_get_token(http_client, "macro_a@test.com", "securepass1")
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    objective_a = await _create_objective(http_client, hdr_a, label="A's meet", target_in_days=30)

    tok_b = await _register_and_get_token(http_client, "macro_b@test.com", "securepass1")
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    resp = await http_client.post(
        "/v1/macrocycles", json={"objective_id": objective_a["id"]}, headers=hdr_b
    )
    assert resp.status_code == 400, resp.text


async def test_macrocycles_unauthenticated(http_client):
    assert (await http_client.get("/v1/macrocycles")).status_code == 401


async def test_patch_delete_nonexistent_macrocycle_404(http_client):
    token = await _register_and_get_token(http_client, "macro_404@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    assert (
        await http_client.patch("/v1/macrocycles/999999", json={"status": "abandoned"}, headers=hdr)
    ).status_code == 404
    assert (await http_client.delete("/v1/macrocycles/999999", headers=hdr)).status_code == 404
