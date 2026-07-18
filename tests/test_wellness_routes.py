"""Route contract tests for /v1/wellness and /v1/readiness (requires a live DB)."""
import pytest
from sqlalchemy import text

from app.services import ekf_shadow_service
from app.services.telemetry_common import best_effort_write

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


async def test_wellness_survives_a_shadow_rollback(http_client, monkeypatch):
    """A best-effort shadow writer that fails + rolls back must not 500 the committed sample.

    ``upsert_wellness_sample`` commits the sample; the shadow writers then run on the same
    session and, on failure, ``best_effort_write`` rolls it back — which EXPIRES the committed
    ORM object. The route materializes its response BEFORE the shadows (wellness.py:38)
    precisely so a later rollback can't trigger an async lazy-load outside a greenlet. This
    pins that guard. Red-capable: move ``model_validate(sample)`` after the shadow calls and
    this 500s.

    We inject the failure into the *last* shadow the route runs (the EKF update) via the real
    ``best_effort_write`` — so exactly one real rollback fires and no later writer reads the
    expired sample (the cross-shadow cascade is a separate concern; see the C14 cluster).
    """
    token = await _register_and_get_token(http_client, "well_shadow_rollback@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    async def _fail_through_best_effort(db, *args, **kwargs):
        # Faithful failure path: the writer does DB work (autobegins a transaction), then
        # fails. Its own best_effort_write rolls that active transaction back once — which
        # expires the just-committed sample — then swallows. The SELECT is what makes the
        # rollback real (a no-op rollback would not expire the sample, and this test would
        # then pass even with the guard removed).
        async with best_effort_write(db, "test-injected shadow failure"):
            await db.execute(text("SELECT 1"))
            raise RuntimeError("injected shadow failure")

    monkeypatch.setattr(
        ekf_shadow_service, "record_ekf_wellness_observation", _fail_through_best_effort
    )

    payload = {"date": "2026-06-23", "source": "manual", "hrv_ms": 61.0, "soreness": 3.0}
    post = await http_client.post("/v1/wellness", json=payload, headers=hdr)
    assert post.status_code == 200, post.text
    assert post.json()["hrv_ms"] == 61.0

    # The sample committed before the shadow rollback and is durable in a fresh request.
    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    assert len(rows) == 1 and rows[0]["date"] == "2026-06-23"


async def test_readiness_no_state_returns_none(http_client):
    token = await _register_and_get_token(http_client, "well_no_state@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/readiness", headers=hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["score"] is None
    assert data["note"] == "no_modeled_state"
    # Confidence is reported even with no modeled state (report-only; ADR-0052).
    assert data["confidence"]["recommendation_gate"]["enforced"] is False
