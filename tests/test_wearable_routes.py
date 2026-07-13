"""Route + service contract tests for /v1/integrations/oura (requires a live DB).

The Oura network layer is replaced with a fake adapter so these exercise the real
persistence, encryption, and wellness-sink wiring without hitting Oura.
"""
from datetime import date

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import settings
from app.integrations.base import NormalizedWellness, TokenBundle
from app.services import wearable_service

pytestmark = pytest.mark.asyncio


class _FakeOura:
    provider = "oura"

    def __init__(self, readings: list[NormalizedWellness]):
        self._readings = readings

    def build_authorize_url(self, state: str) -> str:
        return f"https://fake.oura/authorize?state={state}"

    async def exchange_code(self, code: str) -> TokenBundle:
        return TokenBundle(access_token="acc", refresh_token="ref")

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        return TokenBundle(access_token="acc2", refresh_token="ref2")

    async def fetch_daily_wellness(self, access_token, start, end):
        return self._readings


@pytest.fixture(autouse=True)
def _enc_and_fake_adapter(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", key)
    crypto._fernet_for.cache_clear()
    readings = [
        NormalizedWellness(
            day=date(2026, 7, 1),
            hrv_ms=65.0,
            sleep_hours=7.5,
            sleep_quality=88.0,
            resting_hr=48.0,
            raw={"provider": "oura", "sleep": {"efficiency": 88}},
        )
    ]
    monkeypatch.setattr(
        wearable_service, "_adapter", lambda provider="oura": _FakeOura(readings)
    )
    yield
    crypto._fernet_for.cache_clear()


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


async def test_endpoints_require_auth(http_client):
    assert (await http_client.get("/v1/integrations/oura/connection")).status_code == 401
    assert (await http_client.post("/v1/integrations/oura/sync")).status_code == 401
    assert (await http_client.get("/v1/integrations/oura/authorize")).status_code == 401


async def test_connect_pat_then_sync_writes_wellness(http_client):
    token = await _register_and_get_token(http_client, "oura_sync@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    # Connect via PAT.
    r = await http_client.post(
        "/v1/integrations/oura/connect/pat", json={"token": "pat-abc-12345"}, headers=hdr
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "oura" and body["auth_type"] == "pat"
    assert body["connected"] is True
    # Tokens must never be serialized.
    assert "access_token_enc" not in body and "token" not in body

    # Connecting alone does not write wellness (probe only).
    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    assert not any(row["source"] == "oura" for row in rows)

    # Sync pulls the data in.
    sy = await http_client.post("/v1/integrations/oura/sync", headers=hdr)
    assert sy.status_code == 200, sy.text
    assert sy.json()["rows_written"] == 1

    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    oura = [row for row in rows if row["source"] == "oura"]
    assert len(oura) == 1
    assert oura[0]["hrv_ms"] == 65.0 and oura[0]["resting_hr"] == 48.0


async def test_resync_is_idempotent(http_client):
    token = await _register_and_get_token(http_client, "oura_idem@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    await http_client.post(
        "/v1/integrations/oura/connect/pat", json={"token": "pat-abc-12345"}, headers=hdr
    )
    await http_client.post("/v1/integrations/oura/sync", headers=hdr)
    await http_client.post("/v1/integrations/oura/sync", headers=hdr)

    rows = (await http_client.get("/v1/wellness", headers=hdr)).json()
    oura = [row for row in rows if row["source"] == "oura"]
    assert len(oura) == 1  # upserted on (user, date, source), never duplicated


async def test_connection_status_and_disconnect(http_client):
    token = await _register_and_get_token(http_client, "oura_disc@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    # No connection yet.
    s0 = await http_client.get("/v1/integrations/oura/connection", headers=hdr)
    assert s0.status_code == 200 and s0.json()["connected"] is False

    await http_client.post(
        "/v1/integrations/oura/connect/pat", json={"token": "pat-abc-12345"}, headers=hdr
    )
    s1 = await http_client.get("/v1/integrations/oura/connection", headers=hdr)
    assert s1.json()["connected"] is True

    d = await http_client.delete("/v1/integrations/oura/connection", headers=hdr)
    assert d.status_code == 204

    s2 = await http_client.get("/v1/integrations/oura/connection", headers=hdr)
    assert s2.json()["connected"] is False
    # Sync with no connection is a 404.
    assert (await http_client.post("/v1/integrations/oura/sync", headers=hdr)).status_code == 404


async def test_authorize_returns_url(http_client):
    token = await _register_and_get_token(http_client, "oura_auth@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}
    r = await http_client.get("/v1/integrations/oura/authorize", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["authorize_url"].startswith("https://fake.oura/authorize?state=")
