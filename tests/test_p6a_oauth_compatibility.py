"""P6a's own writers on a database that already holds P6b's attempt history (ADR-0075, R2).

P6b can roll back to P6a by SHA alone, so P6a must keep working on attempts in every status and
on connections past generation 1, and must leave all of it intact: reading the connection, the
legacy OAuth callback reconnecting, a token refresh during sync, and disconnecting.

The legacy writer replaces credentials without advancing ``generation``. That is the documented
gap of the rollback interval, not something this test hides: it pins it.

Also crossing 4: a newer client's PKCE request is refused here rather than silently started
without the binding it asked for.
"""
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.api.v1.integrations_oura import RELOAD_TO_CONNECT
from app.core import crypto
from app.core.config import settings
from app.integrations.base import NormalizedWellness, TokenBundle
from app.services import wearable_service

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 13, 9, 0, 0)
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class _FakeOura:
    provider = "oura"

    def __init__(self) -> None:
        self.exchanges = 0
        self.refreshes = 0

    def build_authorize_url(self, state: str) -> str:
        return f"https://fake.oura/authorize?state={state}"

    async def exchange_code(self, code: str) -> TokenBundle:
        self.exchanges += 1
        return TokenBundle(access_token=f"acc-{code}", refresh_token=f"ref-{code}",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        self.refreshes += 1
        return TokenBundle(access_token="acc-refreshed", refresh_token="ref-refreshed",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def fetch_daily_wellness(self, access_token, start, end):
        return [NormalizedWellness(day=date(2026, 10, 12), hrv_ms=60.0, raw={})]


@pytest.fixture
def fake(monkeypatch) -> _FakeOura:
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    adapter = _FakeOura()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": adapter)
    yield adapter
    crypto._fernet_for.cache_clear()


async def _athlete(client, db, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "pw-123456"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "pw-123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    uid = (await db.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


async def _attempts(db, uid: int, cid: int) -> None:
    """One attempt in every status, as P6b would have left them."""
    shapes = [
        ("pending", None, None, None, None, None),
        ("claimed", T0 + timedelta(minutes=1), None, None, None, None),
        ("succeeded", T0 + timedelta(minutes=1), T0 + timedelta(minutes=2), None, cid, 3),
        ("failed", None, T0 + timedelta(minutes=2), "cancelled", None, None),
        ("failed", T0 + timedelta(minutes=1), T0 + timedelta(minutes=2), "exchange_failed",
         None, None),
    ]
    for i, (status, claimed, outcome, error, receipt_cid, gen) in enumerate(shapes):
        await db.execute(text(
            "INSERT INTO oauth_authorizations (user_id, provider, state_hash, code_challenge, "
            "redirect_uri, status, created_at, expires_at, claimed_at, outcome_at, error, "
            "receipt_connection_id, receipt_generation, connection_id) VALUES (:u, 'oura', :h, "
            ":c, 'https://perflab.test/settings/oura-callback', :s, :t0, :exp, :cl, :out, :err, "
            ":rc, :g, :rc)"
        ), {"u": uid, "h": f"{i:064x}", "c": CHALLENGE, "s": status, "t0": T0,
            "exp": T0 + timedelta(minutes=10), "cl": claimed, "out": outcome, "err": error,
            "rc": receipt_cid, "g": gen})
    await db.commit()


async def _history(db, uid: int) -> list[tuple]:
    rows = (await db.execute(text(
        "SELECT state_hash, status, claimed_at, outcome_at, error, receipt_connection_id, "
        "receipt_generation, connection_id FROM oauth_authorizations WHERE user_id = :u "
        "ORDER BY state_hash"), {"u": uid})).all()
    return [tuple(r) for r in rows]


async def _connection(db, uid: int):
    return (await db.execute(text(
        "SELECT id, generation, access_token_enc FROM wearable_connections WHERE user_id = :u"
    ), {"u": uid})).one_or_none()


async def test_p6a_writers_run_on_p6b_history_and_leave_it_intact(http_client, async_db, fake):
    headers, uid = await _athlete(http_client, async_db, "p6a-compat@test.com")
    cb = await http_client.get("/v1/integrations/oura/callback",
                               params={"code": "c1", "state": wearable_service.sign_state(uid)})
    assert cb.status_code == 302 and cb.headers["location"].endswith("?oura=connected")
    cid, gen, _ = await _connection(async_db, uid)
    assert gen == 1
    # P6b relinked it twice before the rollback.
    await async_db.execute(text("UPDATE wearable_connections SET generation = 3 WHERE id = :c"),
                           {"c": cid})
    await async_db.commit()
    await _attempts(async_db, uid, cid)
    history = await _history(async_db, uid)

    # Read.
    status = await http_client.get("/v1/integrations/oura/connection", headers=headers)
    assert status.status_code == 200 and status.json()["connected"] is True

    # Legacy reconnect: credentials replaced, generation NOT advanced (the rollback-interval gap),
    # history untouched.
    cb = await http_client.get("/v1/integrations/oura/callback",
                               params={"code": "c2", "state": wearable_service.sign_state(uid)})
    assert cb.status_code == 302 and cb.headers["location"].endswith("?oura=connected")
    cid2, gen2, enc2 = await _connection(async_db, uid)
    assert (cid2, gen2) == (cid, 3)
    assert crypto.decrypt(enc2) == "acc-c2"
    assert await _history(async_db, uid) == history

    # Refresh during sync.
    await async_db.execute(text(
        "UPDATE wearable_connections SET expires_at = '2020-01-01' WHERE id = :c"), {"c": cid})
    await async_db.commit()
    sync = await http_client.post("/v1/integrations/oura/sync", headers=headers)
    assert sync.status_code == 200, sync.text
    assert fake.refreshes == 1
    cid3, gen3, enc3 = await _connection(async_db, uid)
    assert (cid3, gen3) == (cid, 3) and crypto.decrypt(enc3) == "acc-refreshed"
    assert await _history(async_db, uid) == history

    # Disconnect: allowed; the receipt keeps its identity, only the live reference goes NULL.
    gone = await http_client.delete("/v1/integrations/oura/connection", headers=headers)
    assert gone.status_code == 204
    assert await _connection(async_db, uid) is None
    after = await _history(async_db, uid)
    succeeded = [r for r in after if r[1] == "succeeded"]
    assert succeeded and all(r[5] == cid and r[6] == 3 and r[7] is None for r in succeeded)
    assert [r[:7] for r in after] == [r[:7] for r in history]

    # Recreate after the disconnect: a new row, starting again at generation 1.
    cb = await http_client.get("/v1/integrations/oura/callback",
                               params={"code": "c3", "state": wearable_service.sign_state(uid)})
    assert cb.status_code == 302
    cid4, gen4, _ = await _connection(async_db, uid)
    assert cid4 != cid and gen4 == 1
    assert await _history(async_db, uid) == after


@pytest.mark.parametrize("params", [
    {"code_challenge": CHALLENGE, "code_challenge_method": "S256"},
    {"code_challenge": CHALLENGE},
    {"code_challenge_method": "S256"},
])
async def test_a_pkce_client_is_told_to_reload_not_downgraded(http_client, async_db, fake, params):
    headers, _ = await _athlete(http_client, async_db, f"p6a-pkce-{len(params)}-{next(iter(params))}@test.com")
    resp = await http_client.get("/v1/integrations/oura/authorize", headers=headers, params=params)
    assert resp.status_code == 400
    assert resp.json()["detail"] == RELOAD_TO_CONNECT
    assert "authorize_url" not in resp.json()


async def test_an_ordinary_p6a_client_still_gets_the_legacy_flow(http_client, async_db, fake):
    headers, _ = await _athlete(http_client, async_db, "p6a-legacy@test.com")
    resp = await http_client.get("/v1/integrations/oura/authorize", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["authorize_url"].startswith("https://fake.oura/authorize?state=")
