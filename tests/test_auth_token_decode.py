"""Token-decode edge cases for ``get_current_user`` (INT-10).

A JWT whose ``sub`` claim is present but non-numeric must be rejected as a 401
(bad credentials), not blow up as an unhandled 500 from ``int(user_id)``. These
are pure unit tests: the failure is raised during decode, before any DB access,
so no ``async_db`` fixture is required.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from jose import jwt

from app.core.auth import get_current_user
from app.core.config import settings


def _token(sub: object) -> str:
    return jwt.encode({"sub": sub}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


pytestmark = pytest.mark.asyncio


async def test_non_numeric_sub_is_401_not_500():
    """A well-signed token with a non-numeric ``sub`` → 401, not an int() ValueError."""
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=_token("not-an-int"), db=db)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    # The DB must never be touched — we reject before the user lookup.
    db.execute.assert_not_awaited()


async def test_missing_sub_is_401():
    """A token with no ``sub`` claim → 401."""
    token = jwt.encode({}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=db)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_bad_signature_is_401():
    """A token signed with the wrong key → 401."""
    token = jwt.encode({"sub": "1"}, "wrong-signing-key", algorithm=settings.ALGORITHM)
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=db)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
