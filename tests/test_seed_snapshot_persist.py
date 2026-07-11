"""ADR-0059 — initialize_athlete_state seeds live per-axis variance + persists the snapshot."""
import pytest
from sqlalchemy import select

from app.logic import seed_variance as sv
from app.models.user import AthleteProfile, User
from app.services import state_service

pytestmark = pytest.mark.asyncio


async def _user_with_profile(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(AthleteProfile(user_id=u.id))
    await db.commit()
    return u


async def test_seed_applies_live_variance_and_persists_snapshot(async_db):
    user = await _user_with_profile(async_db, "seed1@test.com")

    state = await state_service.initialize_athlete_state(
        async_db, user.id, experience_level="intermediate", squat_1rm_kg=140.0
    )

    # Live CapacityConfidence carries per-axis, tier-derived variance (the sole
    # runtime authority) — NOT the old uniform seed.
    assert state.capacity_confidence.max_strength == pytest.approx(
        sv.seed_variance("max_strength", sv.TIER_DIRECT_ESTIMATED_ONRAMP)
    )
    assert state.capacity_confidence.skill == pytest.approx(
        sv.seed_variance("skill", sv.TIER_UNSEEDED)
    )
    # a measured/estimated axis is more certain (lower variance) than an unseeded one
    assert state.capacity_confidence.max_strength < state.capacity_confidence.skill

    # The immutable snapshot is persisted as provenance, with the derived rollup.
    result = await async_db.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == user.id)
    )
    profile = result.scalars().first()
    assert profile is not None
    assert profile.seed_policy_version == sv.POLICY_VERSION
    assert profile.seeded_at is not None
    snap = profile.initial_seed_by_axis
    assert snap is not None
    assert snap["by_axis"]["max_strength"]["evidence_tier"] == sv.TIER_DIRECT_ESTIMATED_ONRAMP
    assert snap["by_axis"]["power"]["seed_group"] == "cross_axis:max_strength"
    assert profile.initial_seed_status == "mixed"  # estimated strength + experience priors
