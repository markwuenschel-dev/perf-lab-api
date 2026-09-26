"""The block's own data decides its periodization (phase 7.1, ADR-0071).

* A block goal with an authored template (Running, Calisthenics) gets that template's bands,
  fitted proportionally over its WORKING weeks; every other goal keeps the generic envelope,
  and says so (``block:periodization=generic``).
* A sprint-primary Running block periodizes for Sprinting, which has no template: generic.
* The block's own calendar decides WHEN a week is a deload or the taper; a deload never
  advances the athlete through the template.
* Templates keyed only by an athlete goal stay unreachable until a block can express it.
"""
from __future__ import annotations

import pytest

from app.logic.constraint_labels import describe_constraints
from app.logic.planning import (
    _BLOCK_TEMPLATES,  # pyright: ignore[reportPrivateUsage]
    _TEMPLATES,  # pyright: ignore[reportPrivateUsage]
    GENERIC_PERIODIZATION,
    BlockType,
    PhaseEnvelope,
    periodization_envelope,
    periodization_goal,
)
from app.models.mesocycle import BlockGoal


def _phases(goal: str | None, weeks: int = 8, deload: int = 4) -> list[str]:
    return [periodization_envelope(weeks, w, deload, goal=goal).phase for w in range(1, weeks + 1)]


# ── which goal a block periodizes for ────────────────────────────────────────


@pytest.mark.parametrize(
    ("block_goal", "mix", "expected"),
    [
        ("Running", {"sprinting": 1.0}, "Sprinting"),
        ("Running", {"sprinting": 0.6, "running": 0.4}, "Sprinting"),
        ("Running", {"running": 0.6, "sprinting": 0.4}, "Running"),
        ("Running", {"running": 0.5, "sprinting": 0.5}, "Running"),  # not strictly primary
        ("Running", {}, "Running"),
        ("Running", None, "Running"),
        ("Calisthenics", {"sprinting": 1.0}, "Calisthenics"),  # sprint rule is Running-only
        ("Strength", {"strength": 1.0}, "Strength"),
        (None, None, None),
    ],
)
def test_the_periodization_goal_comes_from_the_block_alone(
    block_goal: str | None, mix: dict[str, float] | None, expected: str | None
) -> None:
    assert periodization_goal(block_goal, mix) == expected


def test_only_block_goals_can_reach_a_template() -> None:
    """A template keyed only by an athlete goal stays reference data until a block can say it."""
    block_goals = {g.value for g in BlockGoal}
    assert set(_BLOCK_TEMPLATES) <= block_goals
    for goal in set(_TEMPLATES) - set(_BLOCK_TEMPLATES):
        assert periodization_envelope(8, 2, 4, goal=goal).source == GENERIC_PERIODIZATION, goal


# ── generic stays exactly as it was ──────────────────────────────────────────

_GENERIC_8 = [
    "accumulation", "accumulation", "accumulation", "deload",
    "intensification", "intensification", "peak", "taper",
]


@pytest.mark.parametrize(
    "goal", [None, "Strength", "Hypertrophy", "Power", "Hyrox", "CrossFit", "General",
             "Recomp", "Sprinting", "Powerlifting", "HalfMarathon"],
)
def test_goals_without_a_block_template_keep_the_generic_envelope(goal: str | None) -> None:
    assert _phases(goal) == _GENERIC_8
    assert periodization_envelope(8, 2, 4, goal=goal) == PhaseEnvelope(
        "accumulation", 1.15, 6.5, 7.5, source=GENERIC_PERIODIZATION
    )


# ── templated blocks ─────────────────────────────────────────────────────────


def test_a_running_block_follows_the_running_template() -> None:
    envs = [periodization_envelope(8, w, 4, goal="Running") for w in range(1, 9)]
    assert [e.phase for e in envs] == [
        "base", "base", "base", "deload", "threshold", "threshold", "race_specific", "taper",
    ]
    assert {e.source for e in envs} == {"running"}
    assert (envs[0].rpe_low, envs[0].rpe_high, envs[0].volume_modifier) == (4.0, 6.0, 1.0)
    # No DELOAD block in the running template: the generic deload band.
    assert (envs[3].rpe_low, envs[3].rpe_high, envs[3].volume_modifier) == (5.0, 6.5, 0.5)
    # Its TAPER block is authored: its band, not the generic taper's.
    assert (envs[7].rpe_low, envs[7].rpe_high, envs[7].volume_modifier) == (5.0, 7.5, 0.55)


def test_a_sprint_primary_running_block_is_generic() -> None:
    goal = periodization_goal("Running", {"sprinting": 1.0})
    assert _phases(goal) == _GENERIC_8


def test_a_calisthenics_block_follows_the_skill_template() -> None:
    assert _phases("Calisthenics") == [
        "prerequisites", "prerequisites", "strength_focus", "deload",
        "strength_focus", "skill", "skill", "taper",
    ]


def test_a_block_matching_the_template_length_is_the_template() -> None:
    """With no deloads and the template's own length, the fit is the identity."""
    template = _TEMPLATES["Running"]
    expected = [
        b.block_type.value for b in template.blocks for _ in range(b.duration_weeks)
    ]
    assert _phases("Running", weeks=template.total_weeks(), deload=0) == expected


# ── the block owns recovery; deloads do not advance the template ─────────────


@pytest.mark.parametrize("goal", ["Running", "Calisthenics"])
def test_a_deload_never_advances_the_template(goal: str) -> None:
    """The working weeks of a block with deloads follow the same progression as a block of
    that many working weeks with none: recovery positions are removed, not counted."""
    with_deloads = [p for p in _phases(goal, weeks=12, deload=4) if p not in {"deload", "taper"}]
    without = [p for p in _phases(goal, weeks=len(with_deloads) + 1, deload=0) if p != "taper"]
    assert with_deloads == without


@pytest.mark.parametrize("goal", [None, "Running", "Calisthenics"])
def test_recovery_weeks_sit_where_the_block_puts_them(goal: str | None) -> None:
    phases = _phases(goal, weeks=12, deload=3)
    assert [w for w, p in enumerate(phases, 1) if p == "deload"] == [3, 6, 9]
    assert phases[-1] == "taper"


def test_the_workload_preference_moves_template_working_bands_not_recovery() -> None:
    medium = periodization_envelope(8, 1, 4, "medium", goal="Running")
    hard = periodization_envelope(8, 1, 4, "hard", goal="Running")
    assert (hard.rpe_low, hard.rpe_high) == (medium.rpe_low + 0.5, medium.rpe_high + 0.5)
    assert hard.source == "running"
    for week in (4, 8):
        assert periodization_envelope(8, week, 4, "hard", goal="Running") == periodization_envelope(
            8, week, 4, "medium", goal="Running"
        )


# ── the athlete is told ──────────────────────────────────────────────────────


def test_every_block_type_and_source_has_an_athlete_label() -> None:
    codes = [f"block:phase={t.value}(×1.00)" for t in BlockType]
    codes += [f"block:periodization={s}" for s in (GENERIC_PERIODIZATION, *(g.lower() for g in _BLOCK_TEMPLATES))]
    for detail in describe_constraints(codes):
        assert detail.athlete_visible and "Another planning rule" not in detail.label, detail


# ── through the real block and prescribe path ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mix", "days_ago", "source", "phase"),
    # The default Running week starts on day 2; a sprint-mix week on day 1.
    [({}, 1, "running", "base"), ({"sprinting": 1.0}, 0, "generic", "accumulation")],
    ids=["distance_running_block", "sprint_primary_running_block"],
)
async def test_a_running_blocks_own_mix_decides_its_periodization(
    async_db, seeded_exercise_catalog, mix: dict[str, float], days_ago: int, source: str,
    phase: str,
) -> None:
    """The block's modality mix travels create_block_with_sessions -> block context ->
    periodization_goal -> the one envelope, for both the explanation and load sizing."""
    from datetime import date, timedelta

    from app.models.user import AthleteProfile, User
    from app.schemas.planning import BlockCreateRequest
    from app.services.planning_service import create_block_with_sessions, get_today_session
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email=f"periodization-{source}@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    await create_block_with_sessions(async_db, user.id, BlockCreateRequest(
        goal=BlockGoal.RUNNING, start_date=date.today() - timedelta(days=days_ago),
        sessions_per_week=3, duration_weeks=8, modality_mix=mix,
    ))
    assert await get_today_session(async_db, user.id) is not None

    rx = await prescribe_for_athlete(async_db, user.id, "Running")

    assert rx.why is not None
    codes = rx.why.constraints_applied
    assert f"block:periodization={source}" in codes, codes
    assert any(c.startswith(f"block:phase={phase}(") for c in codes), codes
