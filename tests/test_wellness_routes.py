"""Route contract tests for /v1/wellness and /v1/readiness (requires a live DB)."""
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


async def test_wellness_unauthenticated(http_client):
    assert (await http_client.get("/v1/wellness")).status_code == 401
    assert (await http_client.get("/v1/readiness")).status_code == 401


async def test_ingest_then_list_wellness(http_client):
    token = await _register_and_get_token(http_client, "well_ingest@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    payload = {
        "date": "2026-06-23",
        "source": "manual",
        "hrv_ms": 72.0,
        "sleep_hours": 7.5,
        "soreness": 2.0,
        "mood": 7.0,
    }
    post = await http_client.post("/v1/wellness", json=payload, headers=hdr)
    assert post.status_code == 200, post.text
    body = post.json()
    assert body["hrv_ms"] == 72.0 and body["source"] == "manual"

    got = await http_client.get("/v1/wellness", headers=hdr)
    assert got.status_code == 200, got.text
    rows = got.json()
    assert len(rows) == 1 and rows[0]["date"] == "2026-06-23"


async def test_ingest_is_idempotent_per_day_source(http_client):
    token = await _register_and_get_token(http_client, "well_upsert@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    base = {"date": "2026-06-23", "source": "manual"}

    await http_client.post("/v1/wellness", json={**base, "hrv_ms": 50.0}, headers=hdr)
    await http_client.post("/v1/wellness", json={**base, "hrv_ms": 80.0}, headers=hdr)

    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    assert len(rows) == 1  # upserted, not duplicated
    assert rows[0]["hrv_ms"] == 80.0


async def test_readiness_no_state_returns_none(http_client):
    token = await _register_and_get_token(http_client, "well_no_state@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/readiness", headers=hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["readiness"] is None
    assert data["note"] == "no_modeled_state"
