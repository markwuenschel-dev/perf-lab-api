"""PDR-0010 — non-blocking onboarding state machine."""
from types import SimpleNamespace

import pytest

from app.logic import onboarding_state as ob
from app.models.user import AthleteProfile, User
from app.services import onboarding_service as onb
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


# ── pure state machine ────────────────────────────────────────────────────────

def test_required_basics_missing_is_only_safety_gate() -> None:
    from datetime import date

    complete = SimpleNamespace(
        primary_goal="Strength", equipment=["barbell"], available_days_per_week=4,
        date_of_birth=date(1990, 1, 1),
    )
    assert ob.required_basics_missing(complete) == []
    assert ob.can_prescribe(complete) is True
    # precision inputs (1RM/5K) are never a gate — only the safety basics are
    bare = SimpleNamespace(
        primary_goal=None, equipment=[], available_days_per_week=0, date_of_birth=None,
    )
    missing = ob.required_basics_missing(bare)
    assert set(missing) == {"primary_goal", "equipment", "available_days_per_week", "date_of_birth"}
    assert ob.can_prescribe(bare) is False


def test_dob_validation_and_minor_flag() -> None:
    from datetime import date

    today = date(2026, 7, 11)
    # future / implausible DOB rejected
    with pytest.raises(ValueError):
        ob.validate_dob(date(2027, 1, 1), today)
    with pytest.raises(ValueError):
        ob.validate_dob(date(1900, 1, 1), today)  # age > 100
    ob.validate_dob(date(2000, 1, 1), today)  # ok
    # minor is a flag, computed from age — never raises
    assert ob.is_minor(date(2015, 1, 1), today) is True   # 11
    assert ob.is_minor(date(2000, 1, 1), today) is False  # 26
    assert ob.is_minor(None, today) is False
    assert ob.age_from_dob(date(2000, 7, 12), today) == 25  # birthday not yet reached


def test_status_after_basics_advances_but_never_regresses_completed() -> None:
    assert ob.status_after_basics(ob.STATUS_NOT_STARTED) == ob.STATUS_IN_PROGRESS
    assert ob.status_after_basics(ob.STATUS_IN_PROGRESS) == ob.STATUS_IN_PROGRESS
    assert ob.status_after_basics(ob.STATUS_COMPLETED) == ob.STATUS_COMPLETED


def test_completion_reason_validation() -> None:
    assert ob.is_valid_completion_reason("done_for_now")
    assert ob.is_valid_completion_reason("skipped")
    assert not ob.is_valid_completion_reason("quit")


# ── DB: state, twin provisionality, non-blocking exit ─────────────────────────

async def _user_with_profile(db, email, **profile_kw) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(AthleteProfile(user_id=u.id, **profile_kw))
    await db.commit()
    return u


async def test_no_profile_is_not_started_and_not_prescribable(async_db):
    u = User(email="onb0@test.com", hashed_password="x", is_active=True)
    async_db.add(u)
    await async_db.commit()
    await async_db.refresh(u)

    state = await onb.get_onboarding_state(async_db, u.id)
    assert state.status == ob.STATUS_NOT_STARTED
    assert state.can_prescribe is False
    assert "primary_goal" in state.missing_basics
    assert state.twin.seeded is False and state.twin.provisional is True


async def test_seeded_experience_prior_twin_is_provisional_but_usable(async_db):
    from datetime import date

    user = await _user_with_profile(
        async_db, "onb1@test.com", primary_goal="Powerlifting", equipment=["barbell"],
        available_days_per_week=4, date_of_birth=date(1990, 1, 1),
    )
    # experience-prior seed, no benchmarks → usable but provisional (PDR-0010)
    await initialize_athlete_state(async_db, user.id, experience_level="intermediate")

    state = await onb.get_onboarding_state(async_db, user.id)
    assert state.can_prescribe is True          # basics present → the twin is usable
    assert state.missing_basics == []
    assert state.twin.seeded is True
    assert state.twin.provisional is True        # no measurement yet → provisional
    assert state.twin.seed_status in ("experience_prior_only", "mixed")


async def test_complete_is_non_blocking_and_records_reason(async_db):
    user = await _user_with_profile(async_db, "onb2@test.com", primary_goal=None, equipment=[])

    # A user may leave even with basics incomplete — leaving is not failure.
    state = await onb.complete_onboarding(async_db, user.id, "done_for_now")
    assert state.status == ob.STATUS_COMPLETED
    assert state.completed_reason == "done_for_now"
    # …and it never fabricated prescribe-ability the basics don't support.
    assert state.can_prescribe is False


async def test_complete_rejects_bad_reason(async_db):
    user = await _user_with_profile(async_db, "onb3@test.com", primary_goal="Strength", equipment=["barbell"])
    with pytest.raises(ValueError):
        await onb.complete_onboarding(async_db, user.id, "ragequit")
