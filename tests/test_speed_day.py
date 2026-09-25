"""A planned Speed day prescribes sprint work (phase 5.6).

Sprinting stays inside the running DOMAIN (ADR-0038). What says a day is sprint work is its
planned session CATEGORY, "Speed":

    domain = running    category = Speed    (goal = whatever the block says)

The defect this closes: a "sprinting" modality-mix weight was canonicalized to running before
the slot was chosen, so it planned "Aerobic Base" days. And the sprint templates entered the
pool only for the Sprinting goal, which an active block never passes: the prescriber gets the
BLOCK goal (``prescription_service.resolve_effective_goal``), and no BlockGoal is Sprinting.

The gates, as tests:

1. a planned Speed day binds a sprint template, under the block goal the prescriber really
   receives, rather than reporting it unavailable;
2. a "sprinting" mix weight plans running / Speed days, while "running" keeps its days;
3. no other running day can be handed a sprint template, under any running goal;
4. acceleration / max-velocity and speed endurance stay two candidates;
5. the category shown is the session prescribed, through the real planner and pipeline.

Ordinary running days are unchanged byte for byte: the 5.6 commit message records the
before/after comparison over 168 prescriptions and 567 planned weeks.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from test_running_prescriptions import _healthy

from app.logic.candidate_library import SPRINTING_TEMPLATES
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.domain_vocab import canonical_domain
from app.logic.exercise_slot import CatalogExercise
from app.logic.planned_session_slots import SPEED_CATEGORY, binding_for
from app.logic.prescriber import recommend_next_session
from app.schemas.workout_structure import IntervalBlock
from app.services.planning_service import _mix_slot, _template_from_modality_mix

SPRINT_IDS = {t.branch_id for t in SPRINTING_TEMPLATES}
RUNNING_GOALS = ("Running", "5K", "HalfMarathon", "FullMarathon")


def _speed_day(
    catalog: list[CatalogExercise], goal: str, category: str | None = SPEED_CATEGORY,
    kpi: dict[str, float] | None = None,
) -> tuple[object, list[SessionCandidate]]:
    scored: list[SessionCandidate] = []
    block = None if category is None else {
        "block_goal": goal, "session_domain": "running", "session_category": category,
    }
    rx = recommend_next_session(
        _healthy(), goal=goal,  # type: ignore[arg-type]
        kpi_summary=kpi or {}, catalog=catalog, block_context=block, candidate_log_out=scored,
    )
    return rx, scored


# ── 1. the Speed day binds ───────────────────────────────────────────────────


@pytest.mark.parametrize("goal", ["Running", "Sprinting"])
def test_a_speed_day_is_prescribed_a_sprint_session(
    catalog_snapshot: list[CatalogExercise], goal: str
) -> None:
    """"Running" is what an active block passes; "Sprinting" is the athlete goal without one."""
    rx, scored = _speed_day(catalog_snapshot, goal)

    assert scored[0].branch_id in SPRINT_IDS, [c.branch_id for c in scored]
    assert f"plan:session_followed={scored[0].branch_id}" in rx.why.constraints_applied  # type: ignore[attr-defined]
    assert rx.structure and all(isinstance(b, IntervalBlock) for b in rx.structure)  # type: ignore[attr-defined]


def test_the_speed_binding_names_exactly_the_two_sprint_templates() -> None:
    binding = binding_for("running", SPEED_CATEGORY)

    assert binding is not None
    assert set(binding.branch_ids) == SPRINT_IDS == {"run_sprint", "run_speed_endurance"}


# ── 2. the planner writes Speed days ─────────────────────────────────────────


def test_a_sprinting_weight_plans_running_speed_days() -> None:
    assert _mix_slot("sprinting") == ("running", SPEED_CATEGORY, "Running")
    assert _mix_slot("Sprinting") == ("running", SPEED_CATEGORY, "Running")
    assert canonical_domain("sprinting") == "running", "domain unchanged: no new domain"


def test_running_and_sprinting_weights_stay_separate_days() -> None:
    slots = _template_from_modality_mix({"running": 0.6, "sprinting": 0.4}, 5)

    assert slots is not None
    assert sorted(s.category for s in slots) == ["Aerobic Base"] * 3 + [SPEED_CATEGORY] * 2
    assert {s.domain for s in slots} == {"running"}


def test_an_ordinary_running_weight_still_plans_aerobic_base() -> None:
    assert _mix_slot("running") == ("running", "Aerobic Base", "Running")
    assert _mix_slot("HalfMarathon") == ("running", "Aerobic Base", "Running")


# ── 3. nothing else reaches the sprint pool ──────────────────────────────────


@pytest.mark.parametrize("goal", RUNNING_GOALS)
@pytest.mark.parametrize(
    "category", ["Aerobic Base", "Threshold Work", "Active Recovery", "Benchmark Session", None]
)
@pytest.mark.parametrize("kpi", [{}, {"run_fatigue_factor": 20.0}], ids=["nokpi", "ff20"])
def test_no_other_running_day_can_select_a_sprint_template(
    catalog_snapshot: list[CatalogExercise], goal: str, category: str | None,
    kpi: dict[str, float],
) -> None:
    """The WHOLE scored pool, not just the winner: a sprint template that merely competed
    would already have changed ordinary running recommendations."""
    _, scored = _speed_day(catalog_snapshot, goal, category, kpi)

    assert not {c.branch_id for c in scored} & SPRINT_IDS


# ── 4. two qualities, two candidates ─────────────────────────────────────────


def test_both_sprint_qualities_compete_on_a_speed_day(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    _, scored = _speed_day(catalog_snapshot, "Running")

    assert SPRINT_IDS <= {c.branch_id for c in scored}


# ── 5. through the real planner and pipeline (database) ──────────────────────


@pytest.mark.asyncio
async def test_a_sprinting_block_shows_speed_and_prescribes_sprints(
    async_db, seeded_exercise_catalog
) -> None:
    from app.models.mesocycle import BlockGoal, PlannedSession
    from app.models.user import AthleteProfile, User
    from app.schemas.planning import BlockCreateRequest
    from app.services.planning_service import create_block_with_sessions
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email="speed-day@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=[]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    await create_block_with_sessions(async_db, user.id, BlockCreateRequest(
        goal=BlockGoal.RUNNING, start_date=date.today(), sessions_per_week=1,
        modality_mix={"sprinting": 1.0},
    ))

    rx = await prescribe_for_athlete(async_db, user.id, "Sprinting")

    today = (await async_db.execute(
        select(PlannedSession).where(
            PlannedSession.user_id == user.id, PlannedSession.scheduled_date == date.today()
        )
    )).scalar_one()
    assert (today.category, today.domain) == (SPEED_CATEGORY, "running")
    followed = [c for c in rx.why.constraints_applied if c.startswith("plan:")]  # type: ignore[union-attr]
    assert len(followed) == 1 and followed[0].split("=")[1] in SPRINT_IDS, followed
