"""W1-a — only access tokens authenticate requests (requires a live DB).

The wearable OAuth `state` (`wearable_service.sign_state`) is signed with the same key and
algorithm as login tokens. Before this change `get_current_user` checked only signature,
expiry and `sub`, so a `state` value — which travels through Oura's redirect URL — worked
as a 10-minute bearer token. Every case here goes through a real authenticated route so the
seam, not just the helper, is what's proven.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.config import settings
from app.services.wearable_service import sign_state

pytestmark = pytest.mark.asyncio

ROUTE = "/v1/integrations/oura/connection"
TTL = timedelta(minutes=60 * 24 * 7)


async def _register(client, email: str) -> tuple[str, str]:
    """Register + log in; return (access_token, user_id)."""
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]
    claims = jwt.get_unverified_claims(access)
    return access, claims["sub"]


def _sign(claims: dict[str, object]) -> str:
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _status(client, token: str) -> int:
    resp = await client.get(ROUTE, headers={"Authorization": f"Bearer {token}"})
    return resp.status_code


@pytest.fixture
def cutoff(monkeypatch):
    """Typed tokens 'started' one hour ago, with the default 7-day TTL."""
    since = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(settings, "TYPED_TOKENS_SINCE", since)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", int(TTL.total_seconds() // 60))
    return since


async def test_minted_access_token_is_typed_and_accepted(http_client, cutoff):
    token, _ = await _register(http_client, "typ_minted@test.com")
    assert jwt.get_unverified_claims(token)["typ"] == "access"
    assert await _status(http_client, token) == 200


async def test_untyped_token_inside_grace_is_accepted(http_client, cutoff):
    _, sub = await _register(http_client, "typ_legacy_ok@test.com")
    # Issued just before the cutoff with the full TTL → exp inside since + TTL.
    exp = cutoff - timedelta(minutes=5) + TTL
    assert await _status(http_client, _sign({"sub": sub, "exp": exp})) == 200


async def test_untyped_token_past_grace_is_rejected(http_client, cutoff):
    _, sub = await _register(http_client, "typ_legacy_late@test.com")
    # Unexpired, but its exp lies beyond since + TTL: no pre-cutoff issuer could mint it.
    exp = cutoff + TTL + timedelta(minutes=1)
    assert await _status(http_client, _sign({"sub": sub, "exp": exp})) == 401


async def test_untyped_token_rejected_when_no_cutoff_configured(http_client, monkeypatch):
    monkeypatch.setattr(settings, "TYPED_TOKENS_SINCE", None)
    _, sub = await _register(http_client, "typ_no_cutoff@test.com")
    exp = datetime.now(UTC) + timedelta(hours=1)
    assert await _status(http_client, _sign({"sub": sub, "exp": exp})) == 401


async def test_untyped_token_without_exp_is_rejected(http_client, cutoff):
    _, sub = await _register(http_client, "typ_no_exp@test.com")
    assert await _status(http_client, _sign({"sub": sub})) == 401


@pytest.mark.parametrize("typ", ["refresh", "", None, 1, "ACCESS", True])
async def test_wrong_typ_is_rejected(http_client, cutoff, typ):
    _, sub = await _register(http_client, f"typ_wrong_{typ!r}@test.com".replace("'", ""))
    exp = datetime.now(UTC) + timedelta(hours=1)
    assert await _status(http_client, _sign({"sub": sub, "exp": exp, "typ": typ})) == 401


@pytest.mark.parametrize("purpose", ["wearable_oauth", None, "", "anything"])
async def test_purpose_bearing_token_is_rejected_even_if_typed(http_client, cutoff, purpose):
    _, sub = await _register(http_client, f"typ_purpose_{purpose!r}@test.com".replace("'", ""))
    exp = datetime.now(UTC) + timedelta(hours=1)
    claims = {"sub": sub, "exp": exp, "typ": "access", "purpose": purpose}
    assert await _status(http_client, _sign(claims)) == 401


async def test_oauth_state_token_cannot_authenticate(http_client, cutoff):
    """The original hole: a real `sign_state` value presented as a bearer token."""
    _, sub = await _register(http_client, "typ_state@test.com")
    assert await _status(http_client, sign_state(int(sub))) == 401
