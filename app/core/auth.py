"""
app/core/auth.py

JWT + bcrypt utilities. Production-ready and host-agnostic (Railway, any container host).
"""

from datetime import datetime, timedelta
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
    if len(plain) > 72:
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


def create_access_token(
    subject: Any,
    expires_delta: timedelta | None = None,
) -> str:
    """Create JWT access token."""
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": str(subject), "exp": expire}
    token: str = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token


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