from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import AthleteProfile, User
from app.models.weak_point import WeakPoint, WeakPointSource
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.onboarding import (
    CompleteOnboardingRequest,
    OnboardingStateResponse,
    OnboardRequest,
    OnboardResponse,
)
from app.services import onboarding_service
from app.services.state_service import initialize_athlete_state

router = APIRouter(prefix="/v1", tags=["onboarding"])


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_athlete(
    request: OnboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardResponse:
    user = current_user

    # Upsert profile: register creates an empty shell; onboard fills it in.
    profile = await AthleteProfileRepository(db).get_for_user(user.id)

    if profile is None:
        profile = AthleteProfile(user_id=user.id)
        db.add(profile)

    if request.display_name is not None:
        profile.display_name = request.display_name
    profile.primary_goal = request.goal
    profile.date_of_birth = request.date_of_birth
    profile.experience_years = request.experience_years
    profile.experience_level = request.experience_level
    profile.available_days_per_week = request.available_days_per_week
    profile.session_duration_minutes = request.session_duration_minutes
    profile.equipment = request.equipment
    profile.squat_1rm     = request.squat_1rm_kg
    profile.deadlift_1rm  = request.deadlift_1rm_kg
    profile.bench_1rm     = request.bench_1rm_kg
    profile.bodyweight_kg = request.bodyweight_kg
    profile.run_5k_seconds = request.run_5k_seconds

    # Advance the non-blocking state machine: basics submitted → in_progress (PDR-0010).
    await onboarding_service.mark_basics_submitted(db, profile)

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
        deadlift_1rm_kg=request.deadlift_1rm_kg,
        bench_1rm_kg=request.bench_1rm_kg,
        bodyweight_kg=request.bodyweight_kg,
        run_5k_seconds=request.run_5k_seconds,
        experience_years=request.experience_years,
    )

    return OnboardResponse(
        user_id=user.id,
        profile_id=profile.id,
        message="Athlete profile and baseline state ready.",
    )


@router.get("/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingStateResponse:
    """Non-blocking onboarding state (PDR-0010): status, the safety hard-gate
    (`can_prescribe` / `missing_basics`), the provisional twin summary, and progressive
    measurement-debt prompts. Access is never gated on a measurement."""
    return await onboarding_service.get_onboarding_state(db, current_user.id)


@router.post("/onboarding/complete", response_model=OnboardingStateResponse)
async def complete_onboarding(
    request: CompleteOnboardingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingStateResponse:
    """Leave onboarding with a reason (finished | done_for_now | skipped). A user may
    always leave; leaving early is not failure and does not lock them out."""
    try:
        return await onboarding_service.complete_onboarding(
            db, current_user.id, request.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e