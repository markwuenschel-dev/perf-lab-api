"""
Tests for the exercise-aware dose engine.

Verifies:
- Exercise-aware path produces different output than modality-only path
- phi_* fields on ExerciseEntry are used when present
- fallback to modality defaults when no exercises supplied
- AdaptationContribution is populated
- Different exercises (squat vs snatch vs run) produce different dose signatures
"""

from datetime import datetime, timezone

import pytest

from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import ExerciseEntry, WorkoutLog


def _log_base(**kwargs) -> WorkoutLog:
    defaults = dict(
        timestamp=datetime.now(timezone.utc),
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=7.0,
        novelty=1.0,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _squat_phi() -> dict:
    return {
        "phi_adapt": {"strength": 0.55, "hypertrophy": 0.25, "power": 0.05},
        "phi_fatigue": {"cns": 0.3, "muscular": 0.45, "structural": 0.15, "tendon": 0.1},
        "phi_tissue": {"lumbar": 0.3, "hip": 0.25, "knee": 0.25, "ankle": 0.05},
        "energy_mix": {"aerobic": 0.15, "glycolytic": 0.55, "alactic": 0.3},
    }


def _snatch_phi() -> dict:
    return {
        "phi_adapt": {"strength": 0.25, "power": 0.45, "skill": 0.4, "aerobic": 0.05},
        "phi_fatigue": {"cns": 0.55, "muscular": 0.25, "structural": 0.1, "tendon": 0.1},
        "phi_tissue": {"shoulder": 0.15, "wrist": 0.25, "lumbar": 0.1, "hip": 0.15},
        "energy_mix": {"aerobic": 0.05, "glycolytic": 0.25, "alactic": 0.7},
    }


def _run_phi() -> dict:
    return {
        "phi_adapt": {"aerobic": 0.65, "anaerobic": 0.15, "mobility": 0.1},
        "phi_fatigue": {"metabolic": 0.55, "structural": 0.25, "tendon": 0.2},
        "phi_tissue": {"knee": 0.25, "ankle": 0.3, "hip": 0.15},
        "energy_mix": {"aerobic": 0.75, "glycolytic": 0.2, "alactic": 0.05},
    }


# ---------------------------------------------------------------------------
# Fallback behavior (no exercises)
# ---------------------------------------------------------------------------

def test_no_exercises_uses_modality_fallback():
    log = _log_base()
    dose = calculate_stress_dose(log)
    assert dose.dose_six.volume > 0
    assert dose.d_met_systemic >= 0


def test_no_exercises_returns_adaptation_contribution():
    log = _log_base()
    dose = calculate_stress_dose(log)
    ac = dose.adaptation_contribution
    # At least one axis should have a non-zero signal
    total = sum([
        ac.aerobic, ac.glycolytic, ac.max_strength, ac.hypertrophy,
        ac.power, ac.skill, ac.mobility, ac.work_capacity,
    ])
    assert total > 0.0


# ---------------------------------------------------------------------------
# Exercise-aware path
# ---------------------------------------------------------------------------

def test_exercise_aware_dose_differs_from_modality_fallback():
    """Adding exercises with explicit phi changes the dose output."""
    log_no_ex = _log_base(
        estimated_sets=5.0,
        total_volume_load=2000.0,
    )
    sq = _squat_phi()
    log_with_ex = _log_base(
        estimated_sets=5.0,
        total_volume_load=2000.0,
        exercises=[
            ExerciseEntry(
                exercise_name="Back Squat",
                sets=5, reps=3, load_kg=120,
                avg_rpe=8.0,
                **sq,
            )
        ],
    )
    dose_no_ex = calculate_stress_dose(log_no_ex)
    dose_with_ex = calculate_stress_dose(log_with_ex)

    # Some dimension should differ when actual phi vectors are used
    six_no = dose_no_ex.dose_six
    six_with = dose_with_ex.dose_six
    changed = any(
        abs(getattr(six_no, k) - getattr(six_with, k)) > 1e-9
        for k in ("volume", "intensity", "density", "impact", "skill", "metabolic")
    )
    assert changed, "Exercise-aware path must produce different six-axis dose"


def test_squat_and_snatch_produce_different_signatures():
    """Different exercise phi profiles produce distinct dose signatures."""
    sq_phi = _squat_phi()
    sn_phi = _snatch_phi()

    log_squat = _log_base(
        exercises=[
            ExerciseEntry(
                exercise_name="Back Squat",
                sets=5, reps=3, load_kg=120, avg_rpe=8.0,
                **sq_phi,
            )
        ],
    )
    log_snatch = _log_base(
        exercises=[
            ExerciseEntry(
                exercise_name="Snatch",
                sets=5, reps=2, load_kg=80, avg_rpe=8.0,
                **sn_phi,
            )
        ],
    )

    dose_sq = calculate_stress_dose(log_squat)
    dose_sn = calculate_stress_dose(log_snatch)

    # Snatch should have more skill adaptation than squat
    assert dose_sn.adaptation_contribution.skill > dose_sq.adaptation_contribution.skill

    # Squat should have more max_strength adaptation than snatch
    assert dose_sq.adaptation_contribution.max_strength > dose_sn.adaptation_contribution.max_strength


def test_running_exercise_aerobic_dominant():
    """Running exercise phi produces aerobic-dominant dose."""
    run_phi = _run_phi()
    log = _log_base(
        modality="Running",
        exercises=[
            ExerciseEntry(
                exercise_name="Easy Run",
                duration_seconds=2700,
                distance_meters=8000,
                avg_rpe=5.5,
                **run_phi,
            )
        ],
    )
    dose = calculate_stress_dose(log)
    assert dose.adaptation_contribution.aerobic > dose.adaptation_contribution.max_strength


def test_multi_exercise_aggregation():
    """Multiple exercises aggregate into a single session dose."""
    sq_phi = _squat_phi()
    sn_phi = _snatch_phi()

    log = _log_base(
        exercises=[
            ExerciseEntry(exercise_name="Back Squat", sets=4, reps=4, load_kg=100, avg_rpe=7.5, **sq_phi),
            ExerciseEntry(exercise_name="Snatch", sets=5, reps=2, load_kg=70, avg_rpe=7.0, **sn_phi),
        ],
    )
    dose = calculate_stress_dose(log)
    ac = dose.adaptation_contribution

    # Mixed session: both strength and skill adaptation should register
    assert ac.max_strength > 0.0
    assert ac.skill > 0.0
    # Dose_six should be valid
    assert dose.dose_six.volume > 0.0


def test_adaptation_contribution_non_negative():
    """All adaptation contribution axes must be non-negative."""
    log = _log_base(
        exercises=[
            ExerciseEntry(
                exercise_name="Back Squat",
                sets=5, reps=3, load_kg=120, avg_rpe=8.0,
                **_squat_phi(),
            )
        ],
    )
    dose = calculate_stress_dose(log)
    ac = dose.adaptation_contribution
    for key in ac.KEYS:
        assert getattr(ac, key) >= 0.0, f"Negative adaptation on axis {key}"


def test_high_rpe_increases_dose():
    """Higher RPE should produce a higher base dose."""
    log_low = _log_base(session_rpe=5.0, estimated_sets=4.0, total_volume_load=800.0)
    log_high = _log_base(session_rpe=9.0, estimated_sets=4.0, total_volume_load=800.0)

    dose_low = calculate_stress_dose(log_low)
    dose_high = calculate_stress_dose(log_high)

    # Overall load should be higher for higher RPE
    total_low = sum(getattr(dose_low.dose_six, k) for k in ("volume", "intensity", "metabolic"))
    total_high = sum(getattr(dose_high.dose_six, k) for k in ("volume", "intensity", "metabolic"))
    assert total_high > total_low
