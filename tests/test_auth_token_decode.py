"""Token-decode edge cases for ``get_current_user`` (INT-10, W1-a).

A JWT whose ``sub`` claim is present but non-numeric must be rejected as a 401
(bad credentials), not blow up as an unhandled 500 from ``int(user_id)``. These
are pure unit tests: the failure is raised during decode, before any DB access,
so no ``async_db`` fixture is required.

Every fixture here is an otherwise-valid typed access token (``typ="access"``,
future ``exp``) with exactly one defect, so each test still exercises the check it
names — not the W1-a type discriminator, which would reject an untyped fixture first.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from jose import jwt

from app.core import auth
from app.core.auth import get_current_user
from app.core.config import settings


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "sub": "1",
        "typ": "access",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    claims.update(overrides)
    return claims


def _token(claims: dict[str, object], key: str | None = None) -> str:
    return jwt.encode(claims, key or settings.SECRET_KEY, algorithm=settings.ALGORITHM)


pytestmark = pytest.mark.asyncio


async def _rejected_401_before_db(token: str) -> bool:
    """True when the token is refused with a 401 during decode, before any DB access."""
    db = AsyncMock()
    try:
        await get_current_user(token=token, db=db)
    except HTTPException as exc:
        return exc.status_code == status.HTTP_401_UNAUTHORIZED and not db.execute.await_count
    return False


def test_fixture_passes_the_type_discriminator():
    """Guards the guard: if the base fixture stopped being a valid access token, every
    test below would pass for the wrong reason (rejected by type, not by its defect)."""
    assert auth._is_access_token(_claims()) is True


async def test_non_numeric_sub_is_401_not_500():
    """A well-signed typed token with a non-numeric ``sub`` → 401, not an int() ValueError."""
    assert await _rejected_401_before_db(_token(_claims(sub="not-an-int")))


async def test_missing_sub_is_401():
    """A typed token with no ``sub`` claim → 401."""
    claims = _claims()
    del claims["sub"]
    assert await _rejected_401_before_db(_token(claims))


async def test_bad_signature_is_401():
    """A typed token signed with the wrong key → 401."""
    assert await _rejected_401_before_db(_token(_claims(), key="wrong-signing-key"))


@pytest.mark.parametrize(
    ("exp", "typed"),
    [
        # Untyped: every extreme value hits the grace comparison (or jose) and is refused.
        (1e20, False), (float("inf"), False), (float("nan"), False), (2**63, False),
        # Typed: a huge finite exp is a validly signed far-future token (accepted — not a
        # malformed one); only non-finite values are malformed.
        (float("inf"), True), (float("nan"), True),
    ],
)
async def test_extreme_exp_is_401_not_500(monkeypatch, exp, typed):
    """An attacker-shaped exp (huge, inf, NaN) must be a 401, never an OverflowError/500 —
    whether it trips python-jose's own int(exp) (inf, typed or not) or W1-a's untyped
    grace comparison. The cutoff is set so the untyped branch is actually reached."""
    monkeypatch.setattr(settings, "TYPED_TOKENS_SINCE", datetime.now(UTC))
    claims = _claims(exp=exp)
    if not typed:
        del claims["typ"]
    try:
        token = _token(claims)
    except (OverflowError, ValueError):
        pytest.skip(f"jose cannot encode exp={exp!r}")
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=db)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_untyped_grace_boundary(monkeypatch):
    """The grace ends exactly at TYPED_TOKENS_SINCE + one TTL: a token issued at the last
    instant the old issuer could run (exp == boundary) is accepted; one second later is not."""
    since = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(settings, "TYPED_TOKENS_SINCE", since)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 7)
    boundary = (since + timedelta(days=7)).timestamp()
    assert auth._is_access_token({"sub": "1", "exp": boundary}) is True
    assert auth._is_access_token({"sub": "1", "exp": boundary + 1}) is False
