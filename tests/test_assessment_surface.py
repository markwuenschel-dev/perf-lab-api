"""ADR-0047 — one domain-filtered benchmark assessment surface + measurement-debt rank."""
import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.objective import Objective, ObjectiveStatus
from app.models.user import AthleteProfile, User
from app.services import assessment_surface_service as ass
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_active_domain_lenses_from_objectives_and_goal() -> None:
    assert ass.active_domain_lenses(["powerlifting"], None) == {"powerlifting"}
    assert ass.active_domain_lenses([None], "Running") == {"running"}  # goal → domain
    assert ass.active_domain_lenses(["Sprinting"], None) == {"running"}  # alias folds
    assert ass.active_domain_lenses([], None) == set()  # nothing declared → show-all


def test_benchmark_utility_prefers_uncertainty_and_coverage() -> None:
    hi_var = {"max_strength": 1.4, "power": 1.4}
    lo_var = {"max_strength": 0.1, "power": 0.1}
    assert ass.benchmark_utility(["max_strength", "power"], hi_var) > ass.benchmark_utility(
        ["max_strength", "power"], lo_var
    )
    # broader coverage helps at equal uncertainty
    assert ass.benchmark_utility(["a", "b", "c"], None) > ass.benchmark_utility(["a"], None)


# ── DB: the surface ───────────────────────────────────────────────────────────

async def _seed_defs(db) -> None:
    defs = [
        ("pl_e1rm_squat", "Squat e1RM", "powerlifting", ["max_strength"]),
        ("pl_e1rm_bench", "Bench e1RM", "powerlifting", ["max_strength"]),
        ("pl_e1rm_deadlift", "Deadlift e1RM", "powerlifting", ["max_strength", "hypertrophy"]),
        ("run_5k_time", "5K time", "running", ["aerobic"]),
        ("gym_pullup", "Pull-up max", "gymnastics", ["max_strength", "skill"]),
    ]
    for code, name, domain, targets in defs:
        db.add(BenchmarkDefinition(
            code=code, name=name, domain=domain, metric_type="load", unit="kg",
            better_direction="higher", observation_weight=1.0, state_targets=targets,
        ))
    await db.commit()


async def _user(db, email, *, primary_goal=None) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(AthleteProfile(user_id=u.id, primary_goal=primary_goal))
    await db.commit()
    return u


async def test_surface_filters_by_active_domain(async_db):
    await _seed_defs(async_db)
    user = await _user(async_db, "as1@test.com", primary_goal="Powerlifting")
    await initialize_athlete_state(async_db, user.id)

    surface = await ass.build_assessment_surface(async_db, user.id, "onramp")

    assert surface.mode == "onramp"
    assert "powerlifting" in surface.active_domains
    # The surface is FILTERED to active domains: powerlifting benchmarks are shown;
    # running / gymnastics groups (no lens overlap with {powerlifting}) are dropped.
    shown = {g.domain for g in surface.groups}
    assert "powerlifting" in shown
    assert "running" not in shown and "gymnastics" not in shown
    codes = {c.code: c for g in surface.groups for c in g.cards}
    assert codes["pl_e1rm_squat"].eligible is True
    assert "run_5k_time" not in codes  # filtered out of the surface
    # domain_lenses were curated by the ADR-0057 policy
    assert "strength" in codes["pl_e1rm_squat"].domain_lenses
    # measurement debt: at least one powerlifting benchmark is recommended + ranked
    assert surface.recommended
    assert codes[surface.recommended[0]].recommend_rank == 1
    assert codes[surface.recommended[0]].utility_model_version == "information_gain_proxy_v1"


async def test_surface_show_all_when_no_domains(async_db):
    await _seed_defs(async_db)
    user = await _user(async_db, "as2@test.com")  # no goal, no objectives
    await initialize_athlete_state(async_db, user.id)

    surface = await ass.build_assessment_surface(async_db, user.id, "retest")

    assert surface.active_domains == []
    all_cards = [c for g in surface.groups for c in g.cards]
    assert all(c.eligible for c in all_cards)  # whole catalog shown
    assert {"powerlifting", "running", "gymnastics"} <= {g.domain for g in surface.groups}


async def test_surface_objective_domain_drives_eligibility(async_db):
    await _seed_defs(async_db)
    user = await _user(async_db, "as3@test.com")
    async_db.add(Objective(
        user_id=user.id, label="Sub-20 5K", domain="running",
        status=ObjectiveStatus.ACTIVE, priority=1,
    ))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    surface = await ass.build_assessment_surface(async_db, user.id, "onramp")
    assert "running" in surface.active_domains
    codes = {c.code: c for g in surface.groups for c in g.cards}
    assert codes["run_5k_time"].eligible is True
    assert "pl_e1rm_squat" not in codes  # powerlifting group filtered out


async def test_invalid_mode_rejected(async_db):
    user = await _user(async_db, "as4@test.com")
    with pytest.raises(ValueError):
        await ass.build_assessment_surface(async_db, user.id, "bogus")
