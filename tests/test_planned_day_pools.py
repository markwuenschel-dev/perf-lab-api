"""A day planned as one of these categories gets that kind of session, and no other day can
(phase 5.6-5.7).

``candidate_library._CATEGORY_POOLS`` lets a planned category own its day's pool. The plan
decides WHAT today is; the family and KPI logic decide only which member represents it:

    running / Speed                 -> sprint templates            (test_speed_day.py)
    running / Active Recovery       -> run_recovery: a very easy run, Zone 1
    power   / Strength Potentiation -> power_potentiation: heavy squat + jumps, full recovery
    running / Threshold Work        -> exactly one threshold template, whatever the KPIs

Two guarantees per category, both tested here: on its day the session is the one the athlete
was shown, and on every other day its template does not even compete. The second is what
keeps these additions from changing ordinary recommendations.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_running_prescriptions import _healthy
from test_scoring_goldens import _athlete

from app.logic.candidate_library import _CATEGORY_POOLS, GOAL_TEMPLATE_LIBRARY
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.domain_vocab import is_canonical_domain
from app.logic.exercise_slot import CatalogExercise
from app.logic.planned_session_slots import (
    ACTIVE_RECOVERY_CATEGORY,
    STRENGTH_POTENTIATION_CATEGORY,
)
from app.logic.prescriber import recommend_next_session
from app.schemas.prescription import WorkoutPrescription
from app.schemas.workout_structure import ContinuousBlock, StrengthBlock

RUNNING_GOALS = ("Running", "5K", "HalfMarathon", "FullMarathon", "Sprinting")
KPIS = {"nokpi": {}, "ff10": {"run_fatigue_factor": 10.0}, "ff20": {"run_fatigue_factor": 20.0}}


def _day(
    catalog: list[CatalogExercise], goal: str, domain: str, category: str | None,
    kpi: dict[str, float] | None = None, *, fatigued: bool = False,
) -> tuple[WorkoutPrescription, list[SessionCandidate]]:
    """``fatigued`` = CNS fatigue 70, which raises a readiness redirect."""
    scored: list[SessionCandidate] = []
    block = None if category is None else {
        "block_goal": goal, "session_domain": domain, "session_category": category,
    }
    rx = recommend_next_session(
        _athlete(fatigue={"cns": 70.0}) if fatigued else _healthy(),
        goal=goal,  # type: ignore[arg-type]
        kpi_summary=kpi or {}, catalog=catalog, block_context=block, candidate_log_out=scored,
    )
    return rx, scored


def _plan_codes(rx: WorkoutPrescription) -> list[str]:
    assert rx.why is not None
    return [c for c in rx.why.constraints_applied if c.startswith("plan:")]


def test_no_ordinary_day_can_resolve_to_a_category_pool() -> None:
    """The library lists category pools so every guard covers them; no domain may name one."""
    owned = {"running_recovery", "power_potentiation"}
    assert owned <= set(GOAL_TEMPLATE_LIBRARY)
    assert not any(is_canonical_domain(key) for key in owned)
    assert all(not is_canonical_domain(category) for _, category in _CATEGORY_POOLS)


# ── a readiness redirect outranks the plan ───────────────────────────────────


def test_a_fatigued_speed_day_is_pulled_down_to_an_easy_run(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    """Redirects exist to pull work down on a bad day, and the plan must not talk over them.
    A category that owned its pool on that day would leave only sprints to beat the redirect;
    the ordinary running pool rejoins instead, and scoring picks the easy option."""
    from test_speed_day import SPRINT_IDS

    rx, scored = _day(catalog_snapshot, "Running", "running", "Speed", fatigued=True)

    assert scored[0].branch_id not in SPRINT_IDS, [c.branch_id for c in scored]
    assert _plan_codes(rx) == ["plan:session_replaced=running_speed(readiness)"]
    ids = {c.branch_id for c in scored}
    assert set(SPRINT_IDS) <= ids, "still offered, just not chosen"
    assert "run_z2_base" in ids, "the ordinary running pool rejoins on a redirect day"


def test_a_fatigued_recovery_day_keeps_the_very_easy_run(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    """The recovery day IS the pulled-down session. Opening it to the ordinary pool would let
    a 30-40 min zone-2 run beat the very easy one on exactly the day it should not."""
    rx, scored = _day(
        catalog_snapshot, "Running", "running", ACTIVE_RECOVERY_CATEGORY, fatigued=True
    )

    assert scored[0].branch_id == "run_recovery", [c.branch_id for c in scored]
    assert not {c.branch_id for c in scored} & {"run_z2_base", "run_z2_base_threshold"}


# ── running / Active Recovery ────────────────────────────────────────────────


@pytest.mark.parametrize("goal", RUNNING_GOALS)
@pytest.mark.parametrize("kpi", sorted(KPIS))
def test_an_active_recovery_day_is_a_very_easy_run(
    catalog_snapshot: list[CatalogExercise], goal: str, kpi: str
) -> None:
    rx, scored = _day(catalog_snapshot, goal, "running", ACTIVE_RECOVERY_CATEGORY, KPIS[kpi])

    assert [c.branch_id for c in scored] == ["run_recovery"]
    assert _plan_codes(rx) == ["plan:session_followed=run_recovery"]
    assert rx.structure is not None
    (block,) = rx.structure
    assert isinstance(block, ContinuousBlock)
    assert (block.intensity_basis, block.intensity_target) == ("zone", 1.0)
    assert block.activity == "Easy Run"
    # "20-30 min" is a range: no midpoint is picked to time it.
    assert block.duration_sec is None and rx.calculated_duration_min is None


@pytest.mark.parametrize("goal", RUNNING_GOALS)
@pytest.mark.parametrize("category", ["Aerobic Base", "Threshold Work", "Speed", None])
@pytest.mark.parametrize("kpi", sorted(KPIS))
def test_the_recovery_run_competes_on_no_other_running_day(
    catalog_snapshot: list[CatalogExercise], goal: str, category: str | None, kpi: str
) -> None:
    _, scored = _day(catalog_snapshot, goal, "running", category, KPIS[kpi])

    assert "run_recovery" not in {c.branch_id for c in scored}


@pytest.mark.asyncio
async def test_a_running_blocks_recovery_day_is_prescribed_the_recovery_run(
    async_db, seeded_exercise_catalog
) -> None:
    """The default Running week, unedited: Aerobic Base (day 2), Threshold (day 4), Active
    Recovery (day 6). Started five days ago, so today is the recovery day."""
    from app.models.mesocycle import BlockGoal
    from app.models.user import AthleteProfile, User
    from app.schemas.planning import BlockCreateRequest
    from app.services.planning_service import create_block_with_sessions, get_today_session
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email="recovery-day@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=[]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    await create_block_with_sessions(async_db, user.id, BlockCreateRequest(
        goal=BlockGoal.RUNNING, start_date=date.today() - timedelta(days=5),
        sessions_per_week=3,
    ))
    today = await get_today_session(async_db, user.id)
    assert today is not None and today.category == ACTIVE_RECOVERY_CATEGORY

    rx = await prescribe_for_athlete(async_db, user.id, "Running")

    assert _plan_codes(rx) == ["plan:session_followed=run_recovery"]
    assert [e.name for e in rx.exercises] == ["Easy Run"]


# ── power / Strength Potentiation ────────────────────────────────────────────


def test_a_strength_potentiation_day_is_a_heavy_squat_before_jumps(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    rx, scored = _day(catalog_snapshot, "Power", "power", STRENGTH_POTENTIATION_CATEGORY)

    assert [c.branch_id for c in scored] == ["power_potentiation"]
    assert _plan_codes(rx) == ["plan:session_followed=power_potentiation"]
    assert [(e.name, e.sets, e.reps) for e in rx.exercises] == [
        ("Back Squat", 3, "2"), ("Broad Jump", 3, "3"),
    ]
    assert rx.structure is not None
    assert all(isinstance(b, StrengthBlock) for b in rx.structure)
    # The jump's quality rule survives to the athlete (the squat's note is replaced by its
    # resolved load once an e1RM exists).
    assert "Stop or regress" in (rx.exercises[1].load_note or "")


@pytest.mark.parametrize(
    ("goal", "domain", "category"),
    [("Power", "power", c) for c in ("Power Development", "Neural Priming", None)]
    + [("Running", "running", c) for c in ("Aerobic Base", ACTIVE_RECOVERY_CATEGORY, None)]
    + [("Strength", "strength", "Max Strength")],
)
def test_the_potentiation_session_competes_on_no_other_day(
    catalog_snapshot: list[CatalogExercise], goal: str, domain: str, category: str | None
) -> None:
    _, scored = _day(catalog_snapshot, goal, domain, category)

    assert "power_potentiation" not in {c.branch_id for c in scored}


@pytest.mark.asyncio
async def test_a_power_blocks_potentiation_day_is_prescribed_the_contrast_session(
    async_db, seeded_exercise_catalog
) -> None:
    """The default Power week, unedited: Power Development (day 1), Strength Potentiation
    (day 3), Neural Priming (day 5). Started two days ago, so today is day 3."""
    from app.models.mesocycle import BlockGoal
    from app.models.user import AthleteProfile, User
    from app.schemas.planning import BlockCreateRequest
    from app.services.planning_service import create_block_with_sessions, get_today_session
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email="potentiation-day@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    await create_block_with_sessions(async_db, user.id, BlockCreateRequest(
        goal=BlockGoal.POWER, start_date=date.today() - timedelta(days=2), sessions_per_week=3,
    ))
    today = await get_today_session(async_db, user.id)
    assert today is not None and today.category == STRENGTH_POTENTIATION_CATEGORY

    rx = await prescribe_for_athlete(async_db, user.id, "Power")

    assert _plan_codes(rx) == ["plan:session_followed=power_potentiation"]
    assert [e.name for e in rx.exercises] == ["Back Squat", "Broad Jump"]
