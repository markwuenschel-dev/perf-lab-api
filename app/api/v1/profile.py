"""
app/api/v1/profile.py

Read + partial-update the authenticated athlete's profile. ``register`` creates
an empty shell and ``/v1/onboard`` fills it once; this lets the Settings screen
load that data back and edit it any time. Lift/biometric API fields use the
``*_kg`` vocabulary (see ProfileUpdate) and are mapped to the model's columns.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import AthleteProfile, User
from app.schemas.profile import ProfileRead, ProfileUpdate

router = APIRouter(prefix="/v1", tags=["profile"])

# API field name -> AthleteProfile column name (only where they differ).
_COLUMN_MAP = {
    "squat_1rm_kg": "squat_1rm",
    "deadlift_1rm_kg": "deadlift_1rm",
    "bench_1rm_kg": "bench_1rm",
    "overhead_1rm_kg": "overhead_1rm",
}


async def _get_or_create(db: AsyncSession, user_id: int) -> AthleteProfile:
    profile = (
        await db.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = AthleteProfile(user_id=user_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


def _to_read(p: AthleteProfile) -> ProfileRead:
    return ProfileRead(
        display_name=p.display_name,
        experience_years=p.experience_years,
        experience_level=p.experience_level,
        available_days_per_week=p.available_days_per_week,
        session_duration_minutes=p.session_duration_minutes,
        equipment=p.equipment or [],
        squat_1rm_kg=p.squat_1rm,
        deadlift_1rm_kg=p.deadlift_1rm,
        bench_1rm_kg=p.bench_1rm,
        overhead_1rm_kg=p.overhead_1rm,
        pullup_max_reps=p.pullup_max_reps,
        run_5k_seconds=p.run_5k_seconds,
        run_1p5mi_seconds=p.run_1p5mi_seconds,
        bodyweight_kg=p.bodyweight_kg,
        height_cm=p.height_cm,
    )


@router.get("/profile", response_model=ProfileRead)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProfileRead:
    profile = await _get_or_create(db, current_user.id)
    return _to_read(profile)


@router.patch("/profile", response_model=ProfileRead)
async def update_profile(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProfileRead:
    profile = await _get_or_create(db, current_user.id)
    # exclude_unset → true PATCH semantics: untouched fields stay as-is, while an
    # explicit null on a nullable field clears it.
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, _COLUMN_MAP.get(key, key), value)
    await db.commit()
    await db.refresh(profile)
    return _to_read(profile)
