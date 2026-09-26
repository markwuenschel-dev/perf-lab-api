"""A circuit is stations run under a scheme (phase 6.1).

What the scheme owns, as tests:

* **elapsed time** — an AMRAP takes exactly its cap and an EMOM its clock, with station work
  and transitions inside it; a for-time circuit is unknown, and its cap is an UPPER BOUND,
  never known time; fixed rounds are exact only when every station is timed, with the final
  station's transition between rounds and never after the last;
* **the volume lever** — cap, intervals or rounds — as a structural fact, not an easy/hard
  magnitude;
* **its own identity** — a circuit whose projected exercises no longer name its stations is
  refused, never silently rebuilt as strength blocks.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.logic.difficulty import DifficultyDimension, dimensions_changed
from app.logic.dose_engine_v1 import prescribed_timed_work_seconds
from app.logic.exercise_slot import CatalogExercise, CircuitSpec, ExerciseSlot
from app.logic.prescriber import (
    _apply_intensity_sets,  # pyright: ignore[reportPrivateUsage]
    _circuit_realized,  # pyright: ignore[reportPrivateUsage]
    _select_exercises,  # pyright: ignore[reportPrivateUsage]
    _structure_for_selection,  # pyright: ignore[reportPrivateUsage]
)
from app.schemas.prescription import (
    ExercisePrescription,
    WorkoutPrescription,
    project_exercises,
    structure_from_exercises,
)
from app.schemas.workout_structure import (
    AMRAPScheme,
    CircuitBlock,
    CircuitScheme,
    CircuitStation,
    EMOMScheme,
    FixedRoundsScheme,
    ForTimeScheme,
    StrengthBlock,
    WarmupBlock,
    WorkoutStructure,
    adjust_scaled_circuit_rounds,
    apply_volume_modifier,
    calculate_duration,
    emom_exposures,
)


def _stations(**overrides: object) -> list[CircuitStation]:
    wall_ball = {"exercise": "Wall Ball", "reps": 20, "display_sets": 3, "display_reps": "20"}
    ski = {"exercise": "SkiErg", "distance_m": 500.0, "display_sets": 3, "display_reps": "500 m"}
    return [
        CircuitStation.model_validate({**wall_ball, **overrides}),
        CircuitStation.model_validate({**ski, **overrides}),
    ]


def _circuit(scheme: CircuitScheme, **station_overrides: object) -> CircuitBlock:
    return CircuitBlock(label="Engine", stations=_stations(**station_overrides), scheme=scheme)


SCHEMES: dict[str, CircuitScheme] = {
    "amrap": AMRAPScheme(time_cap_sec=720),
    "emom": EMOMScheme(intervals=10, interval_sec=60),
    "for_time": ForTimeScheme(rounds=3, time_cap_sec=900),
    "rounds": FixedRoundsScheme(rounds=3),
}


# ── the model ────────────────────────────────────────────────────────────────


def test_a_scheme_cannot_be_missing_its_own_fields() -> None:
    with pytest.raises(ValidationError):
        CircuitBlock.model_validate(
            {"stations": [{"exercise": "Wall Ball"}], "scheme": {"format": "amrap"}}
        )
    with pytest.raises(ValidationError):
        CircuitBlock.model_validate(
            {"stations": [{"exercise": "Wall Ball"}], "scheme": {"format": "emom", "intervals": 10}}
        )


def test_a_circuit_has_at_least_one_station() -> None:
    with pytest.raises(ValidationError):
        CircuitBlock(stations=[], scheme=AMRAPScheme(time_cap_sec=600))


# ── projection and re-derivation ─────────────────────────────────────────────


@pytest.mark.parametrize("fmt", sorted(SCHEMES))
def test_a_circuit_projects_one_exercise_per_station_and_survives_re_derivation(fmt: str) -> None:
    circuit = _circuit(SCHEMES[fmt])
    exercises = project_exercises([circuit])

    assert [(e.name, e.sets, e.reps) for e in exercises] == [
        ("Wall Ball", 3, "20"),
        ("SkiErg", 3, "500 m"),
    ]
    rx = WorkoutPrescription(
        type="MetCon", focus="f", rationale="r", duration_min=30,
        exercises=exercises, structure=[circuit],
    )
    assert rx.with_structure().structure == [circuit]


def test_station_fields_refresh_from_edited_exercises() -> None:
    circuit = _circuit(SCHEMES["rounds"])
    exercises = project_exercises([circuit])
    exercises[0] = exercises[0].model_copy(
        update={"prescribed_load_kg": 9.0, "weak_point_tags": ["work_capacity"]}
    )

    (rebuilt,) = structure_from_exercises(exercises, [circuit])

    assert isinstance(rebuilt, CircuitBlock)
    assert rebuilt.stations[0].load_target_kg == 9.0
    assert rebuilt.stations[0].weak_point_tags == ["work_capacity"]
    assert rebuilt.stations[0].reps == 20  # the work shape is untouched
    assert project_exercises([rebuilt]) == exercises


def test_an_accessory_appended_after_a_circuit_becomes_its_own_block() -> None:
    circuit = _circuit(SCHEMES["amrap"])
    exercises = [*project_exercises([circuit]), ExercisePrescription(name="Plank", sets=2)]

    rebuilt = structure_from_exercises(exercises, [circuit])

    assert isinstance(rebuilt[0], CircuitBlock)
    assert isinstance(rebuilt[1], StrengthBlock) and rebuilt[1].exercise == "Plank"


def test_a_circuit_whose_exercises_no_longer_name_its_stations_is_refused() -> None:
    circuit = _circuit(SCHEMES["for_time"])
    renamed = project_exercises([circuit])
    renamed[1] = renamed[1].model_copy(update={"name": "Row"})

    with pytest.raises(ValueError, match="disagree"):
        structure_from_exercises(renamed, [circuit])
    with pytest.raises(ValueError, match="disagree"):
        structure_from_exercises(renamed[:1], [circuit])
    with pytest.raises(ValidationError):
        WorkoutPrescription(
            type="MetCon", focus="f", rationale="r", duration_min=30,
            exercises=renamed, structure=[circuit],
        )


def test_legacy_reconstruction_never_invents_a_circuit() -> None:
    exercises = project_exercises([_circuit(SCHEMES["rounds"])])
    assert all(isinstance(b, StrengthBlock) for b in structure_from_exercises(exercises))


# ── duration: the scheme's clock owns elapsed time ───────────────────────────


def test_an_amrap_takes_exactly_its_cap_whatever_its_stations_say() -> None:
    est = calculate_duration(
        [_circuit(AMRAPScheme(time_cap_sec=720), duration_sec=60, transition_sec=15)]
    )
    assert (est.known_seconds, est.complete, est.upper_bound_seconds) == (720, True, None)


def test_an_emom_takes_its_clock() -> None:
    est = calculate_duration([_circuit(EMOMScheme(intervals=10, interval_sec=60))])
    assert (est.known_seconds, est.complete) == (600, True)


def test_a_for_time_cap_is_an_upper_bound_not_a_duration() -> None:
    est = calculate_duration(
        [WarmupBlock(duration_sec=600), _circuit(ForTimeScheme(rounds=3, time_cap_sec=900))]
    )
    assert est.known_seconds == 600  # the cap is NOT known time
    assert est.minutes is None
    assert est.upper_bound_seconds == 1500


def test_an_uncapped_for_time_has_no_bound() -> None:
    est = calculate_duration([_circuit(ForTimeScheme(rounds=3))])
    assert (est.minutes, est.upper_bound_seconds) == (None, None)


def test_a_bound_needs_every_unknown_part_capped() -> None:
    est = calculate_duration(
        [StrengthBlock(exercise="Back Squat", sets=3), _circuit(ForTimeScheme(rounds=3, time_cap_sec=900))]
    )
    assert est.upper_bound_seconds is None


def test_fixed_rounds_count_the_final_transition_between_rounds_only() -> None:
    stations = [
        CircuitStation(exercise="Wall Ball", duration_sec=60, transition_sec=15),
        CircuitStation(exercise="SkiErg", duration_sec=30, transition_sec=20),
    ]
    block = CircuitBlock(stations=stations, scheme=FixedRoundsScheme(rounds=3))
    # 3 × (60 + 15 + 30) work and in-round transitions, then 2 gaps of 20 between rounds.
    assert calculate_duration([block]).known_seconds == 3 * 105 + 2 * 20


def test_one_round_needs_no_final_transition() -> None:
    stations = [
        CircuitStation(exercise="Wall Ball", duration_sec=60, transition_sec=15),
        CircuitStation(exercise="SkiErg", duration_sec=30),
    ]
    est = calculate_duration([CircuitBlock(stations=stations, scheme=FixedRoundsScheme(rounds=1))])
    assert (est.known_seconds, est.complete) == (105, True)


@pytest.mark.parametrize(
    "stations",
    [
        [CircuitStation(exercise="A", duration_sec=60), CircuitStation(exercise="B", duration_sec=30, transition_sec=20)],
        [CircuitStation(exercise="A", duration_sec=60, transition_sec=15), CircuitStation(exercise="B", duration_sec=30)],
        [CircuitStation(exercise="A", transition_sec=15), CircuitStation(exercise="B", duration_sec=30, transition_sec=20)],
    ],
    ids=["within_transition_unknown", "between_rounds_unknown", "work_unknown"],
)
def test_fixed_rounds_are_unknown_when_any_part_is_untimed(stations: list[CircuitStation]) -> None:
    est = calculate_duration([CircuitBlock(stations=stations, scheme=FixedRoundsScheme(rounds=3))])
    assert (est.known_seconds, est.complete) == (0, False)


# ── volume: which lever, not how far ─────────────────────────────────────────


def test_volume_scales_an_amraps_cap() -> None:
    (scaled,) = apply_volume_modifier([_circuit(AMRAPScheme(time_cap_sec=720))], 0.5)
    assert isinstance(scaled, CircuitBlock) and isinstance(scaled.scheme, AMRAPScheme)
    assert scaled.scheme.time_cap_sec == 360
    assert [s.display_sets for s in scaled.stations] == [3, 3]


def test_volume_scales_an_emoms_intervals_and_each_stations_exposures() -> None:
    circuit = _circuit(EMOMScheme(intervals=10, interval_sec=60), display_sets=5)
    (scaled,) = apply_volume_modifier([circuit], 0.5)
    assert isinstance(scaled, CircuitBlock) and isinstance(scaled.scheme, EMOMScheme)
    assert (scaled.scheme.intervals, scaled.scheme.interval_sec) == (5, 60)
    # Stations rotate: 5 intervals over 2 stations is 3 exposures, then 2.
    assert [s.display_sets for s in scaled.stations] == [3, 2]


# ── rotating EMOM ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("intervals", "stations", "expected"),
    [(10, 2, [5, 5]), (10, 3, [4, 3, 3]), (1, 2, [1, 0]), (6, 1, [6])],
)
def test_an_emom_rotates_one_station_per_interval(
    intervals: int, stations: int, expected: list[int]
) -> None:
    scheme = EMOMScheme(intervals=intervals, interval_sec=60)
    assert [emom_exposures(scheme, stations, i) for i in range(stations)] == expected


# ── the workload step moves declared circuit rounds only ─────────────────────


def test_only_rounds_based_circuits_may_scale_with_workload() -> None:
    for scheme in (AMRAPScheme(time_cap_sec=600), EMOMScheme(intervals=10, interval_sec=60)):
        with pytest.raises(ValidationError, match="no rounds"):
            CircuitBlock(stations=_stations(), scheme=scheme, scales_with_workload=True)


def test_the_workload_step_moves_a_declared_circuits_rounds_and_mirrored_sets() -> None:
    declared = CircuitBlock(
        stations=_stations(display_sets=4), scheme=FixedRoundsScheme(rounds=4),
        scales_with_workload=True,
    )
    undeclared = declared.model_copy(update={"scales_with_workload": False})

    harder, untouched = adjust_scaled_circuit_rounds([declared, undeclared], +1)
    (easier,) = adjust_scaled_circuit_rounds([declared], -1)
    (floor,) = adjust_scaled_circuit_rounds([declared], -10)

    assert isinstance(harder, CircuitBlock) and harder.scheme == FixedRoundsScheme(rounds=5)
    assert [s.display_sets for s in harder.stations] == [5, 5]
    assert untouched == undeclared
    assert isinstance(easier, CircuitBlock) and easier.scheme == FixedRoundsScheme(rounds=3)
    assert isinstance(floor, CircuitBlock) and floor.scheme == FixedRoundsScheme(rounds=1)


def _rx(structure: WorkoutStructure) -> WorkoutPrescription:
    return WorkoutPrescription(
        type="Running + Functional", focus="f", rationale="r", duration_min=60,
        exercises=project_exercises(structure), structure=structure,
    )


def test_a_mixed_day_moves_its_declared_circuit_and_leaves_its_strength_sets() -> None:
    squat = StrengthBlock(exercise="Back Squat", sets=5, reps="3")
    circuit = CircuitBlock(
        stations=_stations(display_sets=4), scheme=FixedRoundsScheme(rounds=4),
        scales_with_workload=True,
    )
    rx = _rx([squat, circuit])

    _apply_intensity_sets(rx, "hard", "mixed", is_recovery_week=False)

    assert rx.structure is not None
    moved_squat, moved_circuit = rx.structure
    assert moved_squat == squat  # mixed-domain strength never had a set step
    assert isinstance(moved_circuit, CircuitBlock)
    assert moved_circuit.scheme == FixedRoundsScheme(rounds=5)
    assert [e.sets for e in rx.exercises] == [5, 5, 5]


@pytest.mark.parametrize("workload_volume", ["scaled", "fixed"])
def test_an_undeclared_or_fixed_circuit_does_not_move(workload_volume: str) -> None:
    declared = workload_volume == "fixed"  # a fixed template wins even over a declaration
    circuit = CircuitBlock(
        stations=_stations(display_sets=4), scheme=FixedRoundsScheme(rounds=4),
        scales_with_workload=declared,
    )
    rx = _rx([circuit])

    _apply_intensity_sets(
        rx, "hard", "mixed", is_recovery_week=False,
        workload_volume=workload_volume,  # type: ignore[arg-type]
    )

    assert rx.structure == [circuit]


def test_volume_scales_for_time_rounds_and_leaves_the_cap() -> None:
    (scaled,) = apply_volume_modifier([_circuit(ForTimeScheme(rounds=4, time_cap_sec=900))], 0.5)
    assert isinstance(scaled, CircuitBlock) and isinstance(scaled.scheme, ForTimeScheme)
    assert (scaled.scheme.rounds, scaled.scheme.time_cap_sec) == (2, 900)


def test_volume_scales_fixed_rounds_and_the_sets_that_mirror_them() -> None:
    (scaled,) = apply_volume_modifier([_circuit(FixedRoundsScheme(rounds=3))], 0.5)
    assert isinstance(scaled, CircuitBlock) and isinstance(scaled.scheme, FixedRoundsScheme)
    assert scaled.scheme.rounds == 2
    assert [s.display_sets for s in scaled.stations] == [2, 2]
    assert project_exercises([scaled])[0].sets == 2


# ── difficulty and dose ──────────────────────────────────────────────────────


def test_scaling_a_circuit_moves_volume_only() -> None:
    before: WorkoutStructure = [_circuit(FixedRoundsScheme(rounds=3))]
    assert dimensions_changed(before, apply_volume_modifier(before, 2.0)) == {
        DifficultyDimension.VOLUME
    }


def test_tightening_transitions_is_density_and_changing_format_is_a_different_session() -> None:
    before: WorkoutStructure = [_circuit(FixedRoundsScheme(rounds=3), transition_sec=30)]
    tighter: WorkoutStructure = [_circuit(FixedRoundsScheme(rounds=3), transition_sec=10)]
    other_format: WorkoutStructure = [_circuit(ForTimeScheme(rounds=3), transition_sec=30)]
    assert dimensions_changed(before, tighter) == {DifficultyDimension.DENSITY}
    assert DifficultyDimension.EXERCISE_SELECTION in dimensions_changed(before, other_format)


def test_dose_v1_does_not_model_circuits_yet() -> None:
    assert prescribed_timed_work_seconds([_circuit(SCHEMES["emom"])]) == (
        None,
        "circuit_not_modelled",
    )


# ── emission ─────────────────────────────────────────────────────────────────

_SQUAT = ExerciseSlot(sets="3", reps="2", e1rm_code="pl_e1rm_squat")
_JUMP = ExerciseSlot(sets="3", reps="3", movement_pattern="jump", prefer_tags=("plyometric",))
_NOTHING = ExerciseSlot(sets="3", reps="3", movement_pattern="no_such_pattern")


def _spec(n: int = 2) -> CircuitSpec:
    return CircuitSpec(
        scheme=FixedRoundsScheme(rounds=3),
        stations=tuple(CircuitStation(exercise="", reps=r) for r in (2, 3)[:n]),
        label="Contrast",
    )


def test_a_template_circuit_is_emitted_as_one_block(catalog_snapshot: list[CatalogExercise]) -> None:
    slots = [_SQUAT, _JUMP]
    selection = _select_exercises(slots, None, catalog_snapshot)

    blocks = _structure_for_selection(selection, _spec(), slots)

    assert len(blocks) == 1 and isinstance(blocks[0], CircuitBlock)
    assert [s.exercise for s in blocks[0].stations] == [e.name for e in selection.exercises]
    assert [s.reps for s in blocks[0].stations] == [2, 3]
    assert project_exercises(blocks) == selection.exercises
    assert _circuit_realized(selection, _spec(), slots)


def test_a_circuit_missing_a_station_is_not_emitted(catalog_snapshot: list[CatalogExercise]) -> None:
    slots = [_SQUAT, _NOTHING]
    selection = _select_exercises(slots, None, catalog_snapshot)

    blocks = _structure_for_selection(selection, _spec(), slots)

    assert all(isinstance(b, StrengthBlock) for b in blocks)
    assert not _circuit_realized(selection, _spec(), slots)
