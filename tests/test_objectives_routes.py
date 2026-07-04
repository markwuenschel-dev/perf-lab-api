"""Route contract tests for /v1/objectives (requires a live DB).

Create → list (with progress/days_to_go) → patch status → delete round trip,
for both a benchmark-linked and a free-text objective. Mirrors the
http_client + real-auth-flow pattern in tests/test_wellness_routes.py.
"""
from datetime import date, timedelta

import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation

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


async def _mk_benchmark_definition(async_db, code: str = "route_5k_time") -> BenchmarkDefinition:
    bd = BenchmarkDefinition(
        code=code,
        name="5k Time Trial",
        domain="running",
        metric_type="time",
        unit="seconds",
        better_direction="lower",
    )
    async_db.add(bd)
    await async_db.commit()
    await async_db.refresh(bd)
    return bd


async def test_free_text_objective_create_list_patch_delete(http_client):
    token = await _register_and_get_token(http_client, "obj_free@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    target = (date.today() + timedelta(days=30)).isoformat()

    create_resp = await http_client.post(
        "/v1/objectives",
        json={"label": "First Hyrox", "target_date": target, "priority": 1},
        headers=hdr,
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["benchmark_code"] is None
    assert created["progress"] == {"current": None, "target": None, "pct": None, "direction": None}
    assert created["days_to_go"] == 30

    list_resp = await http_client.get("/v1/objectives", headers=hdr)
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert len(listed) == 1
    assert listed[0]["label"] == "First Hyrox"

    obj_id = created["id"]
    patch_resp = await http_client.patch(
        f"/v1/objectives/{obj_id}", json={"status": "achieved"}, headers=hdr
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["status"] == "achieved"

    # Active-by-default list no longer includes it once achieved.
    active_list = (await http_client.get("/v1/objectives", headers=hdr)).json()
    assert active_list == []

    achieved_list = (
        await http_client.get("/v1/objectives", params={"status": "achieved"}, headers=hdr)
    ).json()
    assert len(achieved_list) == 1

    delete_resp = await http_client.delete(f"/v1/objectives/{obj_id}", headers=hdr)
    assert delete_resp.status_code == 204

    gone = (
        await http_client.get("/v1/objectives", params={"status": "achieved"}, headers=hdr)
    ).json()
    assert gone == []


async def test_benchmark_linked_objective_has_direction_aware_progress(http_client, async_db):
    token = await _register_and_get_token(http_client, "obj_bench@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    definition = await _mk_benchmark_definition(async_db)

    create_resp = await http_client.post(
        "/v1/objectives",
        json={"label": "Sub-24 5k", "benchmark_code": definition.code, "target_value": 1440.0},
        headers=hdr,
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    # domain defaults from the linked benchmark definition
    assert created["domain"] == "running"
    assert created["progress"] == {
        "current": None,
        "target": 1440.0,
        "pct": None,
        "direction": "lower",
    }

    # Post an observation faster than target (lower is better) for this user.
    users_resp = await http_client.get("/v1/objectives", headers=hdr)
    user_id = users_resp.json()[0]["user_id"]
    async_db.add(
        BenchmarkObservation(
            user_id=user_id,
            benchmark_definition_id=definition.id,
            raw_value=1380.0,  # faster than the 1440s target
        )
    )
    await async_db.commit()

    list_resp = await http_client.get("/v1/objectives", headers=hdr)
    assert list_resp.status_code == 200
    progress = list_resp.json()[0]["progress"]
    assert progress["current"] == 1380.0
    assert progress["direction"] == "lower"
    assert progress["pct"] == 100.0  # already beat the target


async def test_objectives_unauthenticated(http_client):
    assert (await http_client.get("/v1/objectives")).status_code == 401


async def test_patch_delete_nonexistent_objective_404(http_client):
    token = await _register_and_get_token(http_client, "obj_404@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    assert (
        await http_client.patch("/v1/objectives/999999", json={"priority": 2}, headers=hdr)
    ).status_code == 404
    assert (await http_client.delete("/v1/objectives/999999", headers=hdr)).status_code == 404
