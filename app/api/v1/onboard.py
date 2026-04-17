from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.db import get_db
from app.models.user import User, AthleteProfile
from app.models.weak_point import WeakPoint, WeakPointSource
from app.schemas.onboarding import OnboardRequest, OnboardResponse
from app.services.state_service import initialize_athlete_state

router = APIRouter(prefix="/v1", tags=["onboarding"])


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_athlete(request: OnboardRequest, db: AsyncSession = Depends(get_db)):
    # 1. Get or create user
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(email=request.email, hashed_password="temp_placeholder")  # replace later with real auth
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # 2. Create AthleteProfile (this was the missing piece)
    profile = AthleteProfile(
        user_id=user.id,
        experience_years=request.experience_years,
        experience_level=request.experience_level,
        available_days_per_week=request.available_days_per_week,
        session_duration_minutes=request.session_duration_minutes,
        equipment=request.equipment,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    # 3. Optional: create self-reported weak points
    for tag in request.self_reported_weak_points:
        wp = WeakPoint(
            user_id=user.id,
            tag=tag,
            source=WeakPointSource.SELF_REPORT,
            confidence=0.6,
            note="Self-reported during onboarding"
        )
        db.add(wp)
    await db.commit()

    # 4. Seed baseline athlete state immediately so first /next-session is ready
    await initialize_athlete_state(
        db,
        user.id,
        experience_level=request.experience_level,
        squat_1rm_kg=request.squat_1rm_kg,
    )

    return OnboardResponse(
        user_id=user.id,
        profile_id=profile.id,
        message="Athlete profile and baseline state created.",
    )