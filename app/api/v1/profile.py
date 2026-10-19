"""
app/api/v1/profile.py

Read + partial-update the authenticated athlete's profile. ``register`` creates
an empty shell and ``/v1/onboard`` fills it once; this lets the Settings screen
load that data back and edit it any time. Lift/biometric API fields use the
``*_kg`` vocabulary (see ProfileUpdate) and are mapped to the model's columns.

Squat, bench and deadlift are not editable here (S2 decision 3). Their columns are a
projection of characterized strength evidence written only by
``strength_evidence_service``; PATCH refuses them, so there is one write authority.
"""

from typing import cast

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.athlete_calendar import TimezoneSource
from app.models.user import AthleteProfile, User
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.profile import ProfileRead, ProfileUpdate, TimezoneCapture
from app.services import athlete_calendar_service

router = APIRouter(prefix="/v1", tags=["profile"])

# API field name -> AthleteProfile column name (only where they differ). Squat, bench and
# deadlift are absent on purpose: ProfileUpdate refuses them (S2 single write authority).
_COLUMN_MAP = {
    "overhead_1rm_kg": "overhead_1rm",
}


async def _get_or_create(db: AsyncSession, user_id: int) -> AthleteProfile:
    profile = await AthleteProfileRepository(db).get_for_user(user_id)
    if profile is None:
        profile = AthleteProfile(user_id=user_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


def _to_read(p: AthleteProfile) -> ProfileRead:
    return ProfileRead(
        display_name=p.display_name,
        primary_goal=p.primary_goal,
        date_of_birth=p.date_of_birth,
        experience_years=p.experience_years,
        experience_level=p.experience_level,
        available_days_per_week=p.available_days_per_week,
        session_duration_minutes=p.session_duration_minutes,
        equipment=p.equipment or [],
        equipment_preference=list(p.equipment_preference or []),
        squat_1rm_kg=p.squat_1rm,
        deadlift_1rm_kg=p.deadlift_1rm,
        bench_1rm_kg=p.bench_1rm,
        overhead_1rm_kg=p.overhead_1rm,
        pullup_max_reps=p.pullup_max_reps,
        run_5k_seconds=p.run_5k_seconds,
        run_1p5mi_seconds=p.run_1p5mi_seconds,
        bodyweight_kg=p.bodyweight_kg,
        height_cm=p.height_cm,
        untracked_wellness_signals=list(p.untracked_wellness_signals or []),
        timezone=p.timezone,
        timezone_source=cast(TimezoneSource | None, p.timezone_source),
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
    changes = body.model_dump(exclude_unset=True)
    zone = changes.pop("timezone", None)
    for key, value in changes.items():
        setattr(profile, _COLUMN_MAP.get(key, key), value)
    if zone is not None:
        # B1: the athlete's own choice; it replaces any zone, a browser-captured one included.
        profile.timezone, profile.timezone_source = zone, "athlete"
    await db.commit()
    await db.refresh(profile)
    return _to_read(profile)


@router.post("/profile/timezone/detected", response_model=ProfileRead)
async def capture_detected_timezone(
    body: TimezoneCapture,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProfileRead:
    """The browser's timezone, stored only while the athlete's is unknown (B1).

    Validated against the server's zoneinfo (422 otherwise). A known zone — captured earlier or
    chosen in Settings — is never replaced: the response is the stored profile either way, so
    the client can tell whether its zone was taken by comparing.
    """
    await athlete_calendar_service.capture_browser_zone(db, current_user.id, body.timezone)
    profile = await _get_or_create(db, current_user.id)
    await db.refresh(profile)
    return _to_read(profile)
