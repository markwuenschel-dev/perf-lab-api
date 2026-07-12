"""Goal-aware, less-coarse twin seeds (twin-seed quality)."""
import pytest

from app.domain.vectors import CapacityState
from app.logic import goal_seed_emphasis as gse
from app.models.user import User
from app.services.state_service import initialize_athlete_state

pytestmark_db = pytest.mark.asyncio


# ── pure emphasis ─────────────────────────────────────────────────────────────

def test_domain_for_goal_maps_goals_and_domains() -> None:
    assert gse.domain_for_goal("Powerlifting") == "powerlifting"
    assert gse.domain_for_goal("Running") == "running"
    assert gse.domain_for_goal("running") == "running"  # already canonical
    assert gse.domain_for_goal("Sprinting") == "running"  # alias
    assert gse.domain_for_goal(None) is None
    assert gse.domain_for_goal("nonsense") is None


def test_emphasis_raises_unmeasured_domain_axes_only() -> None:
    x = CapacityState()  # skill/mobility=50, max_strength=100 default... use a low start
    x.max_strength = 5.0
    x.aerobic = 180.0
    # Powerlifting floors max_strength; aerobic untouched (not a PL axis)
    gse.apply_goal_emphasis(x, "Powerlifting", measured_axes=set())
    assert x.max_strength == 35.0
    assert x.aerobic == 180.0


def test_emphasis_never_overrides_a_measured_axis() -> None:
    x = CapacityState()
    x.max_strength = 12.0  # a real (weak) measured squat
    gse.apply_goal_emphasis(x, "Powerlifting", measured_axes={"max_strength"})
    assert x.max_strength == 12.0  # untouched — a floor must not clobber a measurement


def test_emphasis_never_lowers() -> None:
    x = CapacityState()
    x.max_strength = 80.0  # already strong
    gse.apply_goal_emphasis(x, "Powerlifting", measured_axes=set())
    assert x.max_strength == 80.0  # floor is below current → unchanged


def test_running_emphasis_hits_aerobic() -> None:
    x = CapacityState()
    x.aerobic = 180.0
    gse.apply_goal_emphasis(x, "Running", measured_axes=set())
    assert x.aerobic == 360.0


# ── DB: goal-differentiated + bodyweight-driven seeds ─────────────────────────

async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytestmark_db
async def test_seed_differs_by_goal(async_db):
    run = await initialize_athlete_state(
        async_db, (await _user(async_db, "gseed_run@test.com")).id,
        experience_level="beginner", goal="Running",
    )
    pl = await initialize_athlete_state(
        async_db, (await _user(async_db, "gseed_pl@test.com")).id,
        experience_level="beginner", goal="Powerlifting",
    )
    # A runner starts with more aerobic; a powerlifter with more max_strength.
    assert run.capacity_x.aerobic > pl.capacity_x.aerobic
    assert pl.capacity_x.max_strength > run.capacity_x.max_strength


@pytestmark_db
async def test_bodyweight_seeds_strength_when_no_squat(async_db):
    with_bw = await initialize_athlete_state(
        async_db, (await _user(async_db, "gseed_bw@test.com")).id,
        experience_level="intermediate", bodyweight_kg=90.0, goal="General",
    )
    without = await initialize_athlete_state(
        async_db, (await _user(async_db, "gseed_nobw@test.com")).id,
        experience_level="intermediate", goal="General",
    )
    # 90kg × 1.4 = 126kg estimated squat → stronger prior than the flat table (100kg-eq).
    assert with_bw.capacity_x.max_strength > without.capacity_x.max_strength


@pytestmark_db
async def test_beginner_floor_is_not_near_zero(async_db):
    s = await initialize_athlete_state(
        async_db, (await _user(async_db, "gseed_beg@test.com")).id,
        experience_level="beginner", goal="General",
    )
    # (720-400)/21 ≈ 15 — a saner untrained prior than the old ≈4.8.
    assert s.capacity_x.max_strength > 10.0
