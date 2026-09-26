"""The HYROX and CrossFit planned days prescribe their own sessions (phase 6.2, F10-F12).

    mixed / Hyrox Simulation      -> hyrox_half_sim_a | _b: half the race, exactly as raced
    mixed / Running + Functional  -> run_functional_ski | _lunges: 4 x (1 km run -> 1/4 station)
    mixed / Strength + Skill      -> cf_strength_skill_squat | _deadlift: lift -> skill EMOM

What is pinned here:

* the simulation invariant: official run -> station alternation, and official station order
  and volume for every station a simulation includes;
* exact stations: every pinned movement exists, and resolves to itself or to nothing;
* atomic circuits: a station the athlete cannot do makes the WHOLE template ineligible, never
  a partial circuit; an athlete with no equipment profile keeps "assume available";
* each day's shape, and that its templates compete on no other day.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_running_prescriptions import _healthy

from app.logic.candidate_library import (
    _CATEGORY_POOLS,  # pyright: ignore[reportPrivateUsage]
    GOAL_TEMPLATE_LIBRARY,
    HYROX_OFFICIAL_STATIONS,
    HYROX_RUN_M,
    HYROX_SIMULATION_TEMPLATES,
    RUNNING_FUNCTIONAL_TEMPLATES,
    STRENGTH_SKILL_TEMPLATES,
    CandidateTemplate,
)
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.exercise_slot import CatalogExercise, ExerciseSlot, resolve_slot
from app.logic.planned_session_slots import (
    HYROX_SIMULATION_CATEGORY,
    RUNNING_FUNCTIONAL_CATEGORY,
    STRENGTH_SKILL_CATEGORY,
)
from app.logic.prescriber import recommend_next_session
from app.schemas.prescription import WorkoutPrescription, project_exercises
from app.schemas.workout_structure import (
    CircuitBlock,
    ContinuousBlock,
    EMOMScheme,
    FixedRoundsScheme,
    ForTimeScheme,
    IntervalBlock,
    StrengthBlock,
)

_ALL_POOLS: list[CandidateTemplate] = list({
    t.branch_id: t
    for pool in [
        *GOAL_TEMPLATE_LIBRARY.values(),
        *(owned("") for owned in _CATEGORY_POOLS.values()),
    ]
    for t in pool
}.values())
_PINNED: list[tuple[str, ExerciseSlot]] = [
    (t.branch_id, slot) for t in _ALL_POOLS for slot in t.exercise_slots if slot.exercise
]
_HALF_A_KIT = ["skierg", "sled"]
_HALF_B_KIT = ["rower", "dumbbells", "sandbag", "wall_ball"]


def _day(
    catalog: list[CatalogExercise], category: str, *,
    equipment: list[str] | None = None, workload: str = "medium", goal: str = "Hyrox",
) -> tuple[WorkoutPrescription, list[SessionCandidate]]:
    scored: list[SessionCandidate] = []
    rx = recommend_next_session(
        _healthy(), goal=goal,  # type: ignore[arg-type]
        catalog=catalog, available_equipment=equipment, candidate_log_out=scored,
        block_context={
            "block_goal": goal, "session_domain": "mixed", "session_category": category,
            "week_number": 2, "duration_weeks": 8, "deload_every_n_weeks": 4,
            "intensity": workload,
        },
    )
    return rx, scored


def _plan_codes(rx: WorkoutPrescription) -> list[str]:
    assert rx.why is not None
    return [c for c in rx.why.constraints_applied if c.startswith("plan:")]


def _circuit(rx: WorkoutPrescription) -> CircuitBlock:
    assert rx.structure is not None
    (circuit,) = [b for b in rx.structure if isinstance(b, CircuitBlock)]
    return circuit


# ── the simulation invariant ─────────────────────────────────────────────────


@pytest.mark.parametrize("template", HYROX_SIMULATION_TEMPLATES, ids=lambda t: t.branch_id)
def test_a_simulation_keeps_official_alternation_order_and_volume(
    template: CandidateTemplate,
) -> None:
    circuit = template.circuit
    assert circuit is not None and circuit.scheme == ForTimeScheme(rounds=1)
    assert template.workload_volume == "fixed"
    slots = template.exercise_slots
    runs, stations = slots[0::2], slots[1::2]
    assert [s.exercise for s in runs] == ["Run"] * len(runs)
    assert [s.distance_m for s in circuit.stations[0::2]] == [HYROX_RUN_M] * len(runs)

    official_names = [name for name, *_ in HYROX_OFFICIAL_STATIONS]
    first = official_names.index(stations[0].exercise or "")
    included = HYROX_OFFICIAL_STATIONS[first : first + len(stations)]
    assert [s.exercise for s in stations] == [name for name, *_ in included]
    assert [(c.distance_m, c.reps) for c in circuit.stations[1::2]] == [
        (distance_m, reps) for _, distance_m, reps, _ in included
    ]


def test_the_two_halves_together_are_the_whole_race() -> None:
    stations = [
        s.exercise for t in HYROX_SIMULATION_TEMPLATES for s in t.exercise_slots[1::2]
    ]
    assert stations == [name for name, *_ in HYROX_OFFICIAL_STATIONS]


def test_no_compromised_running_session_uses_wall_balls() -> None:
    """The race has no run after the Wall Balls, so they cannot teach running after them."""
    names = {s.exercise for t in RUNNING_FUNCTIONAL_TEMPLATES for s in t.exercise_slots}
    assert "Wall Ball" not in names


@pytest.mark.parametrize("template", RUNNING_FUNCTIONAL_TEMPLATES, ids=lambda t: t.branch_id)
def test_compromised_running_is_a_quarter_station_four_times(template: CandidateTemplate) -> None:
    circuit = template.circuit
    assert circuit is not None and circuit.scheme == FixedRoundsScheme(rounds=4)
    assert circuit.scales_with_workload
    run, station = circuit.stations
    name = template.exercise_slots[1].exercise
    (official,) = [s for s in HYROX_OFFICIAL_STATIONS if s[0] == name]
    assert run.distance_m == HYROX_RUN_M
    assert station.distance_m is not None and official[1] is not None
    assert 4 * station.distance_m == official[1]


# ── exact stations ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("branch", "slot"), _PINNED, ids=lambda v: str(getattr(v, "exercise", v)))
def test_every_exact_pin_exists_and_resolves_to_itself(
    branch: str, slot: ExerciseSlot, catalog_snapshot: list[CatalogExercise]
) -> None:
    res = resolve_slot(slot, catalog_snapshot)
    assert res.chosen is not None, f"{branch}: no catalog movement named {slot.exercise!r}"
    assert res.chosen.name == slot.exercise


def test_an_exact_pin_never_falls_back_to_a_similar_movement(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    without = [e for e in catalog_snapshot if e.name != "Sled Push"]
    slot = ExerciseSlot(sets="1", reps="50 m", exercise="Sled Push", movement_pattern="carry")
    assert resolve_slot(slot, without).chosen is None


def test_an_exact_pin_ignores_weak_points(catalog_snapshot: list[CatalogExercise]) -> None:
    slot = ExerciseSlot(sets="1", reps="1 km", exercise="Run")
    res = resolve_slot(slot, catalog_snapshot, weak_point_tags=frozenset({"single_leg"}))
    assert res.chosen is not None and res.chosen.name == "Run"


def test_a_slot_cannot_pin_twice() -> None:
    with pytest.raises(ValueError, match="not both"):
        ExerciseSlot(sets="1", reps="1", exercise="Back Squat", e1rm_code="pl_e1rm_squat")


# ── atomic circuits ──────────────────────────────────────────────────────────


def test_no_equipment_profile_assumes_everything_is_available(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    rx, scored = _day(catalog_snapshot, HYROX_SIMULATION_CATEGORY, equipment=None)
    assert {c.branch_id for c in scored} == {"hyrox_half_sim_a", "hyrox_half_sim_b"}
    assert _plan_codes(rx) == ["plan:session_followed=hyrox_half_sim_a"]


def test_a_missing_sled_makes_half_a_ineligible_not_partial(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    rx, scored = _day(catalog_snapshot, HYROX_SIMULATION_CATEGORY, equipment=_HALF_B_KIT)
    assert [c.branch_id for c in scored] == ["hyrox_half_sim_b"]
    assert _plan_codes(rx) == ["plan:session_followed=hyrox_half_sim_b"]
    assert [s.exercise for s in _circuit(rx).stations][1::2] == [
        "Rowing (Ergometer)", "Farmer Carry", "Sandbag Lunges", "Wall Ball",
    ]


@pytest.mark.parametrize("equipment", [["skierg"], ["sled", "rower"], ["barbell"]])
def test_without_a_complete_half_the_day_is_visibly_replaced(
    catalog_snapshot: list[CatalogExercise], equipment: list[str]
) -> None:
    """One station short of each half: no simulation, and never part of one."""
    rx, scored = _day(catalog_snapshot, HYROX_SIMULATION_CATEGORY, equipment=equipment)
    assert not {c.branch_id for c in scored} & {"hyrox_half_sim_a", "hyrox_half_sim_b"}
    (code,) = _plan_codes(rx)
    assert code.startswith("plan:session_replaced=hyrox_simulation")
    assert not any(isinstance(b, CircuitBlock) for b in rx.structure or [])


# ── each day's shape ─────────────────────────────────────────────────────────


def test_a_simulation_day_is_one_for_time_circuit_of_runs_and_stations(
    catalog_snapshot: list[CatalogExercise],
) -> None:
    rx, _ = _day(catalog_snapshot, HYROX_SIMULATION_CATEGORY)
    circuit = _circuit(rx)
    assert rx.type == "HYROX Half Simulation — A"
    assert circuit.scheme == ForTimeScheme(rounds=1)
    assert [(e.name, e.reps) for e in rx.exercises] == [
        ("Run", "1 km"), ("SkiErg", "1000 m"), ("Run", "1 km"), ("Sled Push", "50 m"),
        ("Run", "1 km"), ("Sled Pull", "50 m"), ("Run", "1 km"), ("Burpee Broad Jump", "80 m"),
    ]
    # The Run row takes its distance from the station, and the for-time duration stays unknown.
    assert circuit.stations[0].distance_m == HYROX_RUN_M
    assert rx.calculated_duration_min is None
    assert "division isn't recorded" in (rx.exercises[3].load_note or "")
    assert rx.exercises[3].prescribed_load_kg is None


@pytest.mark.parametrize(("workload", "rounds"), [("easy", 3), ("medium", 4), ("hard", 5)])
def test_compromised_running_rounds_follow_the_workload(
    catalog_snapshot: list[CatalogExercise], workload: str, rounds: int
) -> None:
    rx, _ = _day(catalog_snapshot, RUNNING_FUNCTIONAL_CATEGORY, workload=workload)
    assert _plan_codes(rx) == ["plan:session_followed=run_functional_ski"]
    assert _circuit(rx).scheme == FixedRoundsScheme(rounds=rounds)
    assert [(e.name, e.sets, e.reps) for e in rx.exercises] == [
        ("Run", rounds, "1 km"), ("SkiErg", rounds, "250 m"),
    ]


@pytest.mark.parametrize("workload", ["easy", "medium", "hard"])
def test_strength_and_skill_is_a_fixed_lift_then_a_rotating_emom(
    catalog_snapshot: list[CatalogExercise], workload: str
) -> None:
    rx, _ = _day(catalog_snapshot, STRENGTH_SKILL_CATEGORY, workload=workload, goal="CrossFit")
    assert _plan_codes(rx) == ["plan:session_followed=cf_strength_skill_squat"]
    assert rx.structure is not None
    squat, emom = rx.structure
    assert isinstance(squat, StrengthBlock) and (squat.exercise, squat.sets) == ("Back Squat", 5)
    assert isinstance(emom, CircuitBlock)
    assert emom.scheme == EMOMScheme(intervals=10, interval_sec=60)
    assert [(s.exercise, s.display_sets) for s in emom.stations] == [
        ("Double Unders", 5), ("Toes to Bar", 5),
    ]
    assert "stop before" in (rx.exercises[2].reps or "")
    assert project_exercises(rx.structure) == rx.exercises


@pytest.mark.parametrize(
    "templates", [HYROX_SIMULATION_TEMPLATES, RUNNING_FUNCTIONAL_TEMPLATES, STRENGTH_SKILL_TEMPLATES]
)
def test_these_sessions_compete_on_no_other_mixed_day(
    catalog_snapshot: list[CatalogExercise], templates: list[CandidateTemplate]
) -> None:
    ids = {t.branch_id for t in templates}
    for category in ("MetCon", "Engine Work", "Strength Endurance", None):
        _, scored = _day(catalog_snapshot, category)  # type: ignore[arg-type]
        assert not ids & {c.branch_id for c in scored}, category


# ── through the real planner and service ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("goal_name", "days_ago", "category", "branch"),
    [
        ("HYROX", 5, HYROX_SIMULATION_CATEGORY, "hyrox_half_sim_a"),
        ("HYROX", 2, RUNNING_FUNCTIONAL_CATEGORY, "run_functional_ski"),
        ("CROSSFIT", 0, STRENGTH_SKILL_CATEGORY, "cf_strength_skill_squat"),
    ],
)
async def test_a_blocks_planned_day_is_prescribed_its_session(
    async_db, seeded_exercise_catalog, goal_name: str, days_ago: int, category: str,
    branch: str,
) -> None:
    """The default HYROX / CrossFit week, unedited, through create_block_with_sessions and
    prescribe_for_athlete, with the catalog as the seeder writes it (including "Run")."""
    from app.models.mesocycle import BlockGoal
    from app.models.user import AthleteProfile, User
    from app.schemas.planning import BlockCreateRequest
    from app.services.planning_service import create_block_with_sessions, get_today_session
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email=f"{branch}@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)
    goal = BlockGoal[goal_name]
    await create_block_with_sessions(async_db, user.id, BlockCreateRequest(
        goal=goal, start_date=date.today() - timedelta(days=days_ago), sessions_per_week=3,
    ))
    today = await get_today_session(async_db, user.id)
    assert today is not None and today.category == category

    rx = await prescribe_for_athlete(async_db, user.id, goal.value)

    assert _plan_codes(rx) == [f"plan:session_followed={branch}"]
    assert rx.structure is not None
    assert any(isinstance(b, CircuitBlock) for b in rx.structure)
    assert project_exercises(rx.structure) == rx.exercises


# ── Engine Work (F13a) ───────────────────────────────────────────────────────


@pytest.mark.parametrize("workload", ["easy", "medium", "hard"])
def test_engine_work_is_the_authored_bike_session_on_one_bike(
    catalog_snapshot: list[CatalogExercise], workload: str
) -> None:
    """20 min Zone 2, then 4 x (2 min @ RPE 8 / 2 min easy), with no recovery after the last:
    34 timed minutes. The intervals repeat the steady block's bike, and the session does not
    move with the workload preference in phase 6."""
    rx, _ = _day(catalog_snapshot, "Engine Work", workload=workload, goal="CrossFit")
    assert _plan_codes(rx) == ["plan:session_followed=metcon_engine"]
    assert rx.structure is not None
    steady, intervals = rx.structure
    assert isinstance(steady, ContinuousBlock) and isinstance(intervals, IntervalBlock)
    assert (steady.duration_sec, steady.intensity_basis, steady.intensity_target) == (
        1200, "zone", 2.0,
    )
    assert (intervals.repetitions, intervals.work_duration_sec) == (4, 120)
    assert (intervals.recovery_duration_sec, intervals.recovery_after_last_rep) == (120, False)
    assert steady.activity == intervals.activity
    assert rx.calculated_duration_min == 34.0
