"""
app/api/v1/auth.py
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.db import get_db
from app.core.rate_limit import LOGIN_RATE, REGISTER_RATE, rate_limited
from app.models.user import AthleteProfile, User
from app.repositories.user_repository import UserRepository

logger = logging.getLogger("perflab")

router = APIRouter(prefix="/auth", tags=["Auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password cannot exceed 72 bytes (bcrypt limit)")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


_TOO_MANY: dict[int | str, dict[str, Any]] = {
    429: {"description": "Too many attempts from this address; see Retry-After."}
}


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=201,
    # Counted before the body runs (ADR-0076): over the rate, no lookup and no bcrypt.
    dependencies=[Depends(rate_limited("auth.register", REGISTER_RATE))],
    responses=_TOO_MANY,
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create new user + empty AthleteProfile."""
    # Check for duplicate email. get_by_email is an exact-match lookup — the caller
    # must normalize (lower-case) first, matching the storage/lookup below.
    if await UserRepository(db).get_by_email(body.email.lower()):
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        user = User(
            email=body.email.lower(),
            hashed_password=hash_password(body.password),
        )
        db.add(user)
        await db.flush()                    # get ID before creating profile

        profile = AthleteProfile(user_id=user.id)
        db.add(profile)

        await db.commit()                   # ← correct way (no nested transaction)
        await db.refresh(user)
        return user

    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from None

    except Exception as exc:
        await db.rollback()
        # /auth/register is unauthenticated, so this branch is reachable by anyone.
        # The exception text carries driver, SQL, table and constraint detail, which is
        # reconnaissance — it stays in the server log (with the traceback), and the
        # caller gets the same generic message the global handler in app/main.py
        # promises. There is nothing here a legitimate client can act on: the useful
        # signal (duplicate email) is the 409 above (INT-A8).
        logger.exception(
            "Registration failed for a new account: %s", type(exc).__name__, exc_info=exc
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error. Please try again later.",
        ) from exc


@router.post(
    "/token",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limited("auth.token", LOGIN_RATE))],
    responses=_TOO_MANY,
)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    user = await UserRepository(db).get_by_email(form.username.lower())

    # One bcrypt check on every attempt, whether or not the account exists: against the
    # account's hash, or against a fixed dummy hash when there is none (OWASP: no quick
    # exit). The decision comes after the check, and a missing account always fails — a
    # password that happens to match the dummy never yields a token. Registration's 409
    # still says whether an email is registered, so this hardens login only; it does not
    # make account existence private (ADR-0076).
    password_ok = verify_password(
        form.password, user.hashed_password if user is not None else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_ok:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")

    token = create_access_token(subject=user.id)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user