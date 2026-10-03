"""
app/core/auth.py

JWT + bcrypt utilities. Production-ready and host-agnostic.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# python-jose ships no type stubs; ignore the missing-import error only.
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.db import get_db
from app.models.user import User


def hash_password(plain: str) -> str:
    """Hash password using official bcrypt (no passlib)."""
    if not plain or len(plain.strip()) == 0:
        raise ValueError("Password cannot be empty")
    if len(plain.encode("utf-8")) > 72:
        raise ValueError("Password cannot exceed 72 bytes (bcrypt limit)")

    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(plain.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Safe against timing attacks."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# The only token type `get_current_user` accepts. Other JWTs signed with the same key
# (the wearable OAuth `state`, `wearable_service.sign_state`) carry a `purpose` claim and
# no `typ`, and must never authenticate a request.
ACCESS_TOKEN_TYPE = "access"


def create_access_token(
    subject: Any,
    expires_delta: timedelta | None = None,
) -> str:
    """Create JWT access token."""
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": str(subject), "exp": expire, "typ": ACCESS_TOKEN_TYPE}
    token: str = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token


def _untyped_grace_ends() -> datetime | None:
    """The latest ``exp`` an untyped (pre-``typ``) access token may carry, or None when
    no grace applies. See ``Settings.TYPED_TOKENS_SINCE``."""
    since = settings.TYPED_TOKENS_SINCE
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return since + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)


def _is_access_token(payload: dict[str, Any]) -> bool:
    """True only for a payload this module minted as an access token.

    A ``purpose`` claim of any value (null and empty included) marks a non-access token.
    A present ``typ`` must be exactly ``"access"``. A missing ``typ`` is a legacy token,
    accepted only while its ``exp`` lies inside the one-TTL grace window.
    """
    if "purpose" in payload:
        return False
    if "typ" in payload:
        return payload["typ"] == ACCESS_TOKEN_TYPE
    grace_ends = _untyped_grace_ends()
    exp = payload.get("exp")
    if grace_ends is None or not isinstance(exp, int | float) or isinstance(exp, bool):
        return False
    return datetime.fromtimestamp(exp, UTC) <= grace_ends


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: get current authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if not _is_access_token(payload):
            raise credentials_exception
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        # `sub` is attacker-influenced; a non-numeric value must be a 401, not a
        # 500 from an unguarded int() (INT-10).
        user_pk = int(user_id)
    except (JWTError, ValueError):
        raise credentials_exception from None

    result = await db.execute(select(User).where(User.id == user_pk))
    user = result.scalars().first()

    if user is None or not user.is_active:
        raise credentials_exception

    return user