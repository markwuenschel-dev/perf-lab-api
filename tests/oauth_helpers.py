"""Shared pieces for tests of the wearable OAuth transaction (P6b, ADR-0075).

``FakeOuraOAuth`` stands in for the provider the way the real adapter is called: it builds the
consent URL from the attempt's own redirect URI and challenge, and its exchange records the
verifier and redirect URI it was given. It can be held mid-exchange (``hold``) or made to fail.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import text

from app.integrations.base import NormalizedWellness, TokenBundle

#: RFC 7636 appendix B.
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def pkce(seed: str) -> tuple[str, str]:
    """A distinct (verifier, challenge) pair per seed."""
    verifier = hashlib.sha256(seed.encode()).hexdigest()[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class FakeOuraOAuth:
    provider = "oura"

    def __init__(self) -> None:
        self.exchanges: list[dict[str, str]] = []
        self.refreshes = 0
        self.fail_with: Exception | None = None
        self.hold: asyncio.Event | None = None  # set() it to let a held exchange finish
        # Set only by a call that is about to block on its hold. An unheld exchange earlier in
        # the test (a prior connect) must not set it, or a race test would proceed before its
        # worker has claimed, take the claim itself, and wait forever on its own hold.
        self.entered = asyncio.Event()
        self.refresh_hold: asyncio.Event | None = None
        self.refresh_entered = asyncio.Event()

    def build_authorize_url(self, state: str, *, code_challenge: str, redirect_uri: str) -> str:
        q = urlencode({"state": state, "code_challenge": code_challenge,
                       "code_challenge_method": "S256", "redirect_uri": redirect_uri})
        return f"https://fake.oura/authorize?{q}"

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> TokenBundle:
        self.exchanges.append({"code": code, "verifier": code_verifier, "redirect_uri": redirect_uri})
        if self.hold is not None:
            self.entered.set()
            await self.hold.wait()
        if self.fail_with is not None:
            raise self.fail_with
        return TokenBundle(access_token=f"acc-{code}", refresh_token=f"ref-{code}",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        self.refreshes += 1
        if self.refresh_hold is not None:
            self.refresh_entered.set()
            await self.refresh_hold.wait()
        return TokenBundle(access_token="acc-refreshed", refresh_token="ref-refreshed",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def fetch_daily_wellness(self, access_token, start, end):
        return [NormalizedWellness(day=date(2026, 10, 12), hrv_ms=60.0, raw={})]


async def athlete(client, db, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "pw-123456"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "pw-123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    uid = (await db.execute(text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": email})).scalar_one()
    await db.commit()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


async def start(client, headers: dict[str, str], challenge: str = CHALLENGE) -> str:
    """Start an attempt; return the state the consent URL carries."""
    r = await client.get("/v1/integrations/oura/authorize", headers=headers,
                         params={"code_challenge": challenge, "code_challenge_method": "S256"})
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]


async def complete(client, headers, state: str, code: str = "code-1", verifier: str = VERIFIER):
    return await client.post("/v1/integrations/oura/complete", headers=headers,
                             json={"state": state, "code": code, "code_verifier": verifier})


async def connect(client, headers, code: str = "code-1") -> dict:
    """A whole successful link; returns the receipt."""
    r = await complete(client, headers, await start(client, headers), code=code)
    assert r.status_code == 200, r.text
    return r.json()
