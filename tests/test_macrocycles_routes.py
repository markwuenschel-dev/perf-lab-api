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
