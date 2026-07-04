"""
Regression guard for the dose-law parameter lift (behavior-preserving refactor).

The inline dose-law constants in `app/logic/dose_engine_v0.py` were moved into
`EngineParameters` so they become seedable via the parameter-override mechanism.
This module PINS the current dose outputs for four representative sessions so any
future change to those defaults (or an accidental math drift) is caught immediately.

The pinned numbers were captured from the pre-refactor implementation; passing
these assertions proves the lift did not shift a single value.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.engine.parameters import EngineParameters, default_parameters
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import ExerciseEntry, WorkoutLog

TS = datetime(2026, 1, 1, 12, 0, 0)

# Tight tolerance — outputs must be byte-identical to the pre-refactor engine.
_REL = 1e-12
_ABS = 1e-15


def _strength_log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=TS,
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=8.0,
        avg_rir=2.0,
        total_volume_load=4500.0,
        estimated_sets=20.0,
        novelty=1.0,
        sleep_quality=6.0,
        life_stress_inverse=7.0,
    )


def _run_log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=TS,
        modality="Running",
        duration_minutes=45.0,
        session_rpe=6.0,
        distance_meters=8000.0,
        novelty=1.2,
        sleep_quality=8.0,
        life_stress_inverse=8.0,
    )


def _mixed_log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=TS,
        modality="Mixed",
        duration_minutes=50.0,
        session_rpe=7.0,
        avg_rir=3.0,
        total_volume_load=2000.0,
        novelty=1.5,
        sleep_quality=5.0,
        life_stress_inverse=5.0,
        exercises=[
            ExerciseEntry(sets=4, reps=8, load_kg=80.0, avg_rir=2.0, rest_seconds=120.0),
            ExerciseEntry(sets=3, reps=12, duration_seconds=300.0, avg_rpe=7.0, rest_seconds=90.0),
        ],
    )


def _edge_log() -> WorkoutLog:
    # Edge case: minimal fields, no volume load / RIR / exercises, floor-hugging inputs.
    return WorkoutLog(
        timestamp=TS,
        modality="Power",
        duration_minutes=12.0,
        session_rpe=1.0,
    )


# Pinned expected outputs (captured from the pre-refactor engine).
EXPECTED: dict[str, dict[str, dict[str, float]]] = {
    "strength": {
        "six": {
            "volume": 0.3316526351064194,
            "intensity": 0.34491874051067617,
            "density": 0.13929410674469614,
            "impact": 0.13266105404256776,
            "skill": 0.014924368579788874,
            "metabolic": 0.17411763343087017,
        },
        "ac": {
            "aerobic": 0.06633052702128388,
            "glycolytic": 0.06633052702128388,
            "hypertrophy": 0.09949579053192582,
            "max_strength": 0.09949579053192582,
            "mobility": 0.03316526351064194,
            "power": 0.06633052702128388,
            "skill": 0.07462184289894437,
            "work_capacity": 0.0,
        },
        "legacy": {
            "d_met_systemic": 3.273411508500359,
            "d_nm_peripheral": 3.286677613904616,
            "d_nm_central": 3.89857672567596,
            "d_struct_damage": 2.5039773950534663,
            "d_struct_signal": 91.3796749620427,
        },
    },
    "run": {
        "six": {
            "volume": 0.6543023000138428,
            "intensity": 0.4588613532564612,
            "density": 0.9559611526176275,
            "impact": 0.5608305428690081,
            "skill": 0.13595891948339592,
            "metabolic": 1.4530609519787938,
        },
        "ac": {
            "aerobic": 0.934717571448347,
            "glycolytic": 0.5098459480627348,
            "hypertrophy": 0.2549229740313673,
            "max_strength": 0.2549229740313673,
            "mobility": 0.08497432467712246,
            "power": 0.16994864935424492,
            "skill": 0.19119223052352552,
            "work_capacity": 0.0,
        },
        "legacy": {
            "d_met_systemic": 26.078620243408878,
            "d_nm_peripheral": 4.489618444315765,
            "d_nm_central": 5.999187322204845,
            "d_struct_damage": 10.323955576646991,
            "d_struct_signal": 0.0,
        },
    },
    "mixed": {
        "six": {
            "volume": 1.1284110779085903,
            "intensity": 1.0268540808968172,
            "density": 1.8957306108864314,
            "impact": 0.39494387726800656,
            "skill": 0.05642055389542952,
            "metabolic": 0.41864050990408697,
        },
        "ac": {
            "aerobic": 0.7898877545360131,
            "glycolytic": 1.0155699701177312,
            "hypertrophy": 0.338523323372577,
            "max_strength": 0.338523323372577,
            "mobility": 0.11284110779085904,
            "power": 0.22568221558171808,
            "skill": 0.2821027694771476,
            "work_capacity": 0.0,
        },
        "legacy": {
            "d_met_systemic": 17.235350803975805,
            "d_nm_peripheral": 9.862312820921078,
            "d_nm_central": 11.690338767132996,
            "d_struct_damage": 7.503933668092125,
            "d_struct_signal": 79.10741632358727,
        },
    },
    "edge": {
        "six": {
            "volume": 0.04648134563673062,
            "intensity": 0.006042574932774981,
            "density": 0.019522165167426858,
            "impact": 0.004648134563673062,
            "skill": 0.005345354748224021,
            "metabolic": 0.01789531807014129,
        },
        "ac": {
            "aerobic": 0.009296269127346124,
            "glycolytic": 0.009296269127346124,
            "hypertrophy": 0.013944403691019186,
            "max_strength": 0.013944403691019186,
            "mobility": 0.004648134563673062,
            "power": 0.041833211073057555,
            "skill": 0.026726773741120105,
            "work_capacity": 0.0,
        },
        "legacy": {
            "d_met_systemic": 0.3676674439865392,
            "d_nm_peripheral": 0.07994791449517666,
            "d_nm_central": 0.10388580749809294,
            "d_struct_damage": 0.09993489311897084,
            "d_struct_signal": 3.006042574932775,
        },
    },
}

_LOGS = {
    "strength": _strength_log,
    "run": _run_log,
    "mixed": _mixed_log,
    "edge": _edge_log,
}


@pytest.mark.parametrize("name", sorted(_LOGS))
def test_dose_outputs_pinned(name: str) -> None:
    """Dose outputs must exactly match the pre-refactor engine for each session."""
    dose = calculate_stress_dose(_LOGS[name]())
    exp = EXPECTED[name]

    six = dose.dose_six
    for axis, want in exp["six"].items():
        got = getattr(six, axis)
        assert got == pytest.approx(want, rel=_REL, abs=_ABS), f"{name}.six.{axis}"

    ac = dose.adaptation_contribution
    for key, want in exp["ac"].items():
        got = getattr(ac, key)
        assert got == pytest.approx(want, rel=_REL, abs=_ABS), f"{name}.ac.{key}"

    for chan, want in exp["legacy"].items():
        got = getattr(dose, chan)
        assert got == pytest.approx(want, rel=_REL, abs=_ABS), f"{name}.legacy.{chan}"


@pytest.mark.parametrize("name", sorted(_LOGS))
def test_explicit_default_params_match_implicit(name: str) -> None:
    """Passing default_parameters() explicitly is identical to passing nothing."""
    log = _LOGS[name]()
    implicit = calculate_stress_dose(log)
    explicit = calculate_stress_dose(log, params=default_parameters())

    assert explicit.dose_six.model_dump() == implicit.dose_six.model_dump()
    assert (
        explicit.adaptation_contribution.model_dump()
        == implicit.adaptation_contribution.model_dump()
    )


def test_lifted_defaults_have_expected_values() -> None:
    """The lifted parameter defaults still hold the original inline literals."""
    p = default_parameters()
    assert p.dose_volume_weights == {"duration": 1.0, "volume_load": 0.02, "sets": 2.0}
    assert p.dose_delta_sets_multiplier == 5.0
    assert p.dose_delta_min_divisor == 20.0
    assert p.dose_delta_cap == 2.5
    assert p.dose_delta_floor == 0.35
    assert p.dose_novelty_floor == 0.2
    assert p.dose_w_phi_floor == 0.25
    assert p.dose_human_factor_reference == 5.0
    assert p.dose_human_factor_slope == 0.2
    assert p.dose_entry_volume_proxy_weights == {
        "volume_load": 0.005,
        "duration_divisor": 60.0,
        "distance_divisor": 500.0,
        "sets_reps": 0.1,
    }
    assert p.dose_shape_six_by_modality["Running"]["metabolic"] == 0.9
    assert p.dose_shape_six_by_modality["strength"]["intensity"] == 0.65
    assert p.dose_shape_six_by_modality["default"]["volume"] == 0.4


def test_parameter_override_changes_output() -> None:
    """A seeded (non-default) parameter set must actually move the dose — proving
    the lifted constants are now calibratable via the override mechanism."""
    log = _strength_log()
    baseline = calculate_stress_dose(log)

    tuned = EngineParameters()
    tuned.dose_shape_six_by_modality["strength"]["volume"] = 0.9  # was 0.5

    changed = calculate_stress_dose(log, params=tuned)
    assert changed.dose_six.volume != baseline.dose_six.volume
    # Other axes unaffected by the volume-multiplier tweak.
    assert changed.dose_six.intensity == pytest.approx(baseline.dose_six.intensity)
