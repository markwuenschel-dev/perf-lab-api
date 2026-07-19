"""Non-blocking onboarding state (PDR-0010).

Surfaces the persisted onboarding state machine, the provisional twin summary, and
progressive measurement-debt prompts — and lets a user leave at any time. Access is
never gated on a measurement; only ``can_prescribe`` reflects the safety/feasibility
hard gate.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.logic import confidence_presentation as cp
from app.logic import onboarding_state as ob
from app.models.user import AthleteProfile
from app.repositories.athlete_profile_repository import AthleteProfileRepository
from app.schemas.onboarding import (
    OnboardingStateResponse,
    OnboardingTwinSummary,
)
from app.services import assessment_surface_service, state_service

# How many measurement-debt prompts to surface at once (progressive, not a wall).
_DEBT_PROMPT_LIMIT = 5


async def _profile(db: AsyncSession, user_id: int) -> AthleteProfile | None:
    return await AthleteProfileRepository(db).get_for_user(user_id)


async def _twin_summary(db: AsyncSession, user_id: int, profile: AthleteProfile | None) -> OnboardingTwinSummary:
    seed_status = getattr(profile, "initial_seed_status", "none") if profile else "none"
    state = await state_service.load_current_state(db, user_id)
    if state is None:
        return OnboardingTwinSummary(
            seeded=False, seed_status=seed_status, provisional=True, overall_confidence=None
        )
    # Worst-axis live variance → the overall certainty band (ADR-0059: live variance is
    # the sole provisionality authority; the seed snapshot is never read for this).
    worst_var = max(float(getattr(state.capacity_confidence, a)) for a in CapacityState.KEYS)
    band = cp.confidence_status(worst_var)
    provisional = band != cp.STATUS_ESTABLISHED or seed_status != "benchmark_seeded"
    return OnboardingTwinSummary(
        seeded=True, seed_status=seed_status, provisional=provisional, overall_confidence=band
    )


async def get_onboarding_state(db: AsyncSession, user_id: int) -> OnboardingStateResponse:
    profile = await _profile(db, user_id)
    status = getattr(profile, "onboarding_status", ob.STATUS_NOT_STARTED) if profile else ob.STATUS_NOT_STARTED
    completed_reason = getattr(profile, "completed_reason", None) if profile else None
    missing = ob.required_basics_missing(profile) if profile else ["primary_goal", "equipment"]

    twin = await _twin_summary(db, user_id, profile)

    # Progressive measurement-debt prompts from the one assessment surface (never a gate).
    debt: list[str] = []
    try:
        surface = await assessment_surface_service.build_assessment_surface(
            db, user_id, assessment_surface_service.MODE_ONRAMP
        )
        debt = surface.recommended[:_DEBT_PROMPT_LIMIT]
    except Exception:
        debt = []

    minor = ob.is_minor(getattr(profile, "date_of_birth", None), date.today()) if profile else False
    return OnboardingStateResponse(
        status=status,
        completed_reason=completed_reason,
        can_prescribe=(not missing),
        missing_basics=missing,
        is_minor=minor,
        twin=twin,
        measurement_debt=debt,
    )


async def complete_onboarding(
    db: AsyncSession, user_id: int, reason: str
) -> OnboardingStateResponse:
    """Mark onboarding complete with a reason. Non-blocking: a user may leave at any
    time (done_for_now / skipped), whether or not the basics are done."""
    if not ob.is_valid_completion_reason(reason):
        raise ValueError(
            f"reason must be one of {sorted(ob.COMPLETION_REASONS)}, got {reason!r}"
        )
    profile = await _profile(db, user_id)
    if profile is not None:
        profile.onboarding_status = ob.STATUS_COMPLETED
        profile.completed_reason = reason
        await db.commit()
    return await get_onboarding_state(db, user_id)


async def mark_basics_submitted(db: AsyncSession, profile: AthleteProfile) -> None:
    """Advance the state machine once basics are submitted (called from /onboard).

    Advances not_started → in_progress; leaves a completed profile completed. Does not
    commit — the caller's transaction owns it.
    """
    profile.onboarding_status = ob.status_after_basics(profile.onboarding_status)
