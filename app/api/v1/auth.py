"""
app/api/v1/auth.py
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.core.db import get_db
from app.models.user import User, AthleteProfile

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

    class Config:
        from_attributes = True


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> User:
    # Duplicate check
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalars().first():
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        async with db.begin():   # atomic transaction
            user = User(
                email=body.email.lower(),
                hashed_password=hash_password(body.password),   # ← this is the most common crash point
            )
            db.add(user)
            await db.flush()

            profile = AthleteProfile(user_id=user.id)
            db.add(profile)

        await db.refresh(user)
        return user

    except Exception as exc:   # ← catch everything and return real error
        await db.rollback()
        # This will now show the exact Python error in the frontend
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {type(exc).__name__}: {str(exc)}"
        ) from exc