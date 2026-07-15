"""
app/api/v1/auth.py
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, get_current_user, hash_password, verify_password
from app.core.db import get_db
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
        if len(v) > 72:
            raise ValueError("Password cannot exceed 72 characters (bcrypt limit)")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create new user + empty AthleteProfile."""
    # Check for duplicate email
    if await UserRepository(db).get_by_email(body.email):
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


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    user = await UserRepository(db).get_by_email(form.username.lower())

    if not user or not verify_password(form.password, user.hashed_password):
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