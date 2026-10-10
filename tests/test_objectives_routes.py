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
    assert created["progress"] == {
        "current": None, "target": None, "pct": None, "direction": None,
        "current_evidence_type": None, "current_value_semantics": None,
    }
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
        "current_evidence_type": None,
        "current_value_semantics": None,
    }

    # Post an observation faster than target (lower is better) for this user.
    users_resp = await http_client.get("/v1/objectives", headers=hdr)
    user_id = users_resp.json()[0]["user_id"]
    async_db.add(
        BenchmarkObservation(
            user_id=user_id,
            benchmark_definition_id=definition.id,
            raw_value=1380.0,  # faster than the 1440s target
            # What an athlete's own entry is stamped with; an unlabeled row is not attainment.
            validity_status="valid",
            source_type="athlete_entry",
            evidence_type="direct_measurement",
            value_semantics="measured",
        )
    )
    await async_db.commit()

    list_resp = await http_client.get("/v1/objectives", headers=hdr)
    assert list_resp.status_code == 200
    progress = list_resp.json()[0]["progress"]
    assert progress["current"] == 1380.0
    assert progress["direction"] == "lower"
    assert progress["pct"] == 100.0  # already beat the target
    assert (progress["current_evidence_type"], progress["current_value_semantics"]) == (
        "direct_measurement", "measured"
    )


async def test_objectives_unauthenticated(http_client):
    assert (await http_client.get("/v1/objectives")).status_code == 401


async def test_patch_delete_nonexistent_objective_404(http_client):
    token = await _register_and_get_token(http_client, "obj_404@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    assert (
        await http_client.patch("/v1/objectives/999999", json={"priority": 2}, headers=hdr)
    ).status_code == 404
    assert (await http_client.delete("/v1/objectives/999999", headers=hdr)).status_code == 404


# ---------------------------------------------------------------------------
# PUT /v1/objectives/order — display order (display only, never priority)
# ---------------------------------------------------------------------------

async def _mk_objectives(client, hdr, specs: list[tuple[str, int]]) -> list[dict]:
    made = []
    for label, priority in specs:
        resp = await client.post(
            "/v1/objectives", json={"label": label, "priority": priority}, headers=hdr
        )
        assert resp.status_code == 200, resp.text
        made.append(resp.json())
    return made


async def test_order_writes_display_rank_and_leaves_priority(http_client):
    token = await _register_and_get_token(http_client, "obj_order@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    a, b, c = await _mk_objectives(http_client, hdr, [("A", 1), ("B", 2), ("C", 3)])
    assert a["display_rank"] is None  # never ordered

    # Before any ordering: priority order.
    listed = (await http_client.get("/v1/objectives", headers=hdr)).json()
    assert [o["label"] for o in listed] == ["A", "B", "C"]

    new_order = [c["id"], a["id"], b["id"]]
    resp = await http_client.put(
        "/v1/objectives/order", json={"objective_ids": new_order}, headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert [o["id"] for o in resp.json()] == new_order
    assert [o["display_rank"] for o in resp.json()] == [1, 2, 3]

    listed = (await http_client.get("/v1/objectives", headers=hdr)).json()
    assert [o["label"] for o in listed] == ["C", "A", "B"]
    assert {o["label"]: o["priority"] for o in listed} == {"A": 1, "B": 2, "C": 3}
    assert {o["label"]: o["display_rank"] for o in listed} == {"C": 1, "A": 2, "B": 3}


async def test_order_never_ordered_objectives_sort_last(http_client):
    token = await _register_and_get_token(http_client, "obj_nulls@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    a, b = await _mk_objectives(http_client, hdr, [("A", 2), ("B", 3)])
    resp = await http_client.put(
        "/v1/objectives/order", json={"objective_ids": [b["id"], a["id"]]}, headers=hdr
    )
    assert resp.status_code == 200, resp.text

    # Created after ordering, and priority 1 — still lands at the bottom (NULLS LAST).
    (late,) = await _mk_objectives(http_client, hdr, [("Late", 1)])
    listed = (await http_client.get("/v1/objectives", headers=hdr)).json()
    assert [o["label"] for o in listed] == ["B", "A", "Late"]
    assert listed[-1]["id"] == late["id"] and listed[-1]["display_rank"] is None

    # The old order no longer covers every active objective → 400 until the client refetches.
    stale = await http_client.put(
        "/v1/objectives/order", json={"objective_ids": [b["id"], a["id"]]}, headers=hdr
    )
    assert stale.status_code == 400
    assert str(late["id"]) in stale.json()["detail"]


async def test_order_rejects_mismatched_ids(http_client):
    token = await _register_and_get_token(http_client, "obj_bad_order@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    a, b = await _mk_objectives(http_client, hdr, [("A", 1), ("B", 2)])

    other_tok = await _register_and_get_token(http_client, "obj_bad_order2@test.com", "securepass1")
    other_hdr = {"Authorization": f"Bearer {other_tok}"}
    (foreign,) = await _mk_objectives(http_client, other_hdr, [("Theirs", 1)])

    cases = {
        "missing": [a["id"]],
        "extra": [a["id"], b["id"], 999999],
        "foreign": [a["id"], b["id"], foreign["id"]],
        "duplicate": [a["id"], b["id"], a["id"]],
    }
    details = {}
    for name, ids in cases.items():
        resp = await http_client.put(
            "/v1/objectives/order", json={"objective_ids": ids}, headers=hdr
        )
        assert resp.status_code == 400, (name, resp.text)
        details[name] = resp.json()["detail"]
    assert f"missing active objectives [{b['id']}]" in details["missing"]
    assert "999999" in details["extra"]
    assert str(foreign["id"]) in details["foreign"]
    assert f"duplicate ids [{a['id']}]" in details["duplicate"]

    # Nothing was written by any rejected request, and the other user's row is untouched.
    listed = (await http_client.get("/v1/objectives", headers=hdr)).json()
    assert all(o["display_rank"] is None for o in listed)
    theirs = (await http_client.get("/v1/objectives", headers=other_hdr)).json()
    assert theirs[0]["display_rank"] is None


async def test_order_excludes_non_active_objectives(http_client):
    token = await _register_and_get_token(http_client, "obj_order_active@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    a, done = await _mk_objectives(http_client, hdr, [("A", 1), ("Done", 2)])
    patch = await http_client.patch(
        f"/v1/objectives/{done['id']}", json={"status": "achieved"}, headers=hdr
    )
    assert patch.status_code == 200
    # An achieved objective is not part of the active order.
    bad = await http_client.put(
        "/v1/objectives/order", json={"objective_ids": [a["id"], done["id"]]}, headers=hdr
    )
    assert bad.status_code == 400
    ok = await http_client.put("/v1/objectives/order", json={"objective_ids": [a["id"]]}, headers=hdr)
    assert ok.status_code == 200, ok.text
    assert ok.json()[0]["display_rank"] == 1


async def test_order_and_driving_unauthenticated(http_client):
    assert (
        await http_client.put("/v1/objectives/order", json={"objective_ids": []})
    ).status_code == 401
    assert (await http_client.get("/v1/objectives/driving")).status_code == 401
