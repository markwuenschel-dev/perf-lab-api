from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic import observation_authority as oa
from app.models.user import AthleteProfile, User
from app.models.weak_point import WeakPoint, WeakPointSource
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.onboarding import (
    CompleteOnboardingRequest,
    OnboardingStateResponse,
    OnboardRequest,
    OnboardResponse,
)
from app.services import (
    benchmark_service,
    onboarding_service,
    state_service,
    strength_evidence_service,
)

router = APIRouter(prefix="/v1", tags=["onboarding"])


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_athlete(
    request: OnboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardResponse:
    """Profile basics, self-reported weak points, the baseline state, and strength reports.

    **One transaction.** Everything below commits together, or nothing does.

    **Retry-safe.** The server cannot tell a retry whose first attempt committed (and whose
    response was lost) from the same submission sent twice, so both are treated alike: the
    baseline state is seeded only for an athlete with no state; a weak point the athlete has
    already self-reported and not resolved is not added again; a report identical to one
    onboarding already recorded is not recorded again. Profile fields take the submitted values.
    Concurrent submissions for one athlete are serialized on the user row, so the second finds
    the first's writes once it commits.
    """
    user_id = current_user.id

    # Strength reports are checked before anything is written: an unknown lift or a future
    # performance refuses the whole request rather than half-applying it.
    try:
        await strength_evidence_service.check_reports(db, request.strength)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

    # Upsert profile: register creates an empty shell; onboard fills it in. Squat, bench and
    # deadlift are NOT written here — the strength service derives them from the evidence.
    profile = await AthleteProfileRepository(db).get_for_user(user_id)

    if profile is None:
        profile = AthleteProfile(user_id=user_id)
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
    profile.bodyweight_kg = request.bodyweight_kg
    profile.run_5k_seconds = request.run_5k_seconds

    # Advance the non-blocking state machine: basics submitted → in_progress (PDR-0010).
    await onboarding_service.mark_basics_submitted(db, profile)
    await db.flush()
    profile_id = profile.id

    # Self-reported weak points: at most one unresolved self-report per tag.
    already_reported = set((await db.execute(
        select(WeakPoint.tag).where(
            WeakPoint.user_id == user_id,
            WeakPoint.source == WeakPointSource.SELF_REPORT,
            WeakPoint.resolved_at.is_(None),
        )
    )).scalars().all())
    for tag in dict.fromkeys(request.self_reported_weak_points):
        if tag in already_reported:
            continue
        db.add(WeakPoint(
            user_id=user_id,
            tag=tag,
            source=WeakPointSource.SELF_REPORT,
            confidence=0.6,
            note="Self-reported during onboarding",
        ))

    # Seed the baseline state so the first /next-session is ready — only for an athlete with
    # no state, so a repeated submission never replaces state the athlete has since earned.
    # The lift seeds are the SAME derived values the evidence carries — one fact, one
    # derivation — and seeding comes first, so the onramp evidence staged below finds a twin
    # and does not seed it a second time.
    if not await state_service.has_state(db, user_id):
        seed = strength_evidence_service.seed_values(request.strength)
        await state_service.stage_baseline_state(
            db,
            user_id,
            experience_level=request.experience_level,
            squat_1rm_kg=seed.get(strength_evidence_service.SQUAT_E1RM_CODE),
            deadlift_1rm_kg=seed.get(strength_evidence_service.DEADLIFT_E1RM_CODE),
            bench_1rm_kg=seed.get(strength_evidence_service.BENCH_E1RM_CODE),
            bodyweight_kg=request.bodyweight_kg,
            run_5k_seconds=request.run_5k_seconds,
            experience_years=request.experience_years,
            goal=request.goal,
        )

    # Each report becomes characterized strength evidence through the one E1 path, which also
    # derives the profile's squat/bench/deadlift columns.
    staged: list[benchmark_service.StagedObservation] = []
    for report in request.strength:
        recorded = await strength_evidence_service.stage_strength_report(
            db, user_id, report,
            collection_mode=oa.CM_ONBOARDING_ONRAMP,
            skip_if_recorded=True,
        )
        if recorded is not None:
            staged.append(recorded)

    await db.commit()

    for recorded in staged:
        await benchmark_service.complete_observation(db, user_id, recorded)

    return OnboardResponse(
        user_id=user_id,
        profile_id=profile_id,
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
