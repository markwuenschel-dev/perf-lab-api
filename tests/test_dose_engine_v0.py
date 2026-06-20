"""Unit tests for app.logic.dose_engine_v0.calculate_stress_dose.

All tests are pure — no DB required. Tests verify:
- Return type and shape
- Non-negativity of all channels
- Directional invariants (higher RPE → more dose)
- Modality-specific adaptation dominance
- Non-mutation guarantee (simulate-dose invariant, Decision 9)
- Near-zero dose for trivial sessions
"""
from datetime import UTC, datetime

from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import StressDose, WorkoutLog


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


# ── Return type ──────────────────────────────────────────────────────────────

def test_returns_stress_dose_type():
    dose = calculate_stress_dose(_log())
    assert isinstance(dose, StressDose)
    assert dose.dose_six is not None
    assert dose.adaptation_contribution is not None


# ── Channel non-negativity ───────────────────────────────────────────────────

def test_all_legacy_channels_non_negative():
    dose = calculate_stress_dose(_log())
    assert dose.d_met_systemic >= 0.0
    assert dose.d_nm_peripheral >= 0.0
    assert dose.d_nm_central >= 0.0
    assert dose.d_struct_damage >= 0.0
    assert dose.d_struct_signal >= 0.0


def test_all_six_axis_channels_non_negative():
    dose = calculate_stress_dose(_log())
    six = dose.dose_six
    assert six.volume >= 0.0
    assert six.intensity >= 0.0
    assert six.density >= 0.0
    assert six.impact >= 0.0
    assert six.skill >= 0.0
    assert six.metabolic >= 0.0


# ── Directional invariants ───────────────────────────────────────────────────

def test_higher_rpe_increases_total_dose():
    low = calculate_stress_dose(_log(session_rpe=4.0))
    high = calculate_stress_dose(_log(session_rpe=9.0))
    low_total = low.d_met_systemic + low.d_nm_peripheral + low.d_nm_central
    high_total = high.d_met_systemic + high.d_nm_peripheral + high.d_nm_central
    assert high_total > low_total


def test_longer_session_increases_total_dose():
    short = calculate_stress_dose(_log(duration_minutes=20.0))
    long_ = calculate_stress_dose(_log(duration_minutes=90.0))
    short_total = short.d_met_systemic + short.d_nm_central
    long_total = long_.d_met_systemic + long_.d_nm_central
    assert long_total > short_total


# ── Modality-specific adaptation dominance ───────────────────────────────────

def test_running_produces_aerobic_dominant_adaptation():
    dose = calculate_stress_dose(_log(modality="Running", duration_minutes=40.0))
    ac = dose.adaptation_contribution
    assert ac.aerobic > ac.max_strength, (
        f"Running should drive aerobic > max_strength, got aerobic={ac.aerobic:.4f} max_strength={ac.max_strength:.4f}"
    )


def test_strength_session_produces_nonzero_max_strength_adaptation():
    dose = calculate_stress_dose(
        _log(modality="Strength", total_volume_load=5000.0, avg_rir=2.0, estimated_sets=15.0)
    )
    assert dose.adaptation_contribution.max_strength > 0.0


# ── Non-mutation (Decision 9: simulate-dose must be idempotent) ───────────────

def test_simulate_dose_is_non_mutating():
    log = _log(session_rpe=7.0)
    dose1 = calculate_stress_dose(log)
    dose2 = calculate_stress_dose(log)
    assert dose1.d_met_systemic == dose2.d_met_systemic
    assert dose1.d_nm_central == dose2.d_nm_central
    assert dose1.dose_six.metabolic == dose2.dose_six.metabolic


# ── Near-zero dose for trivial sessions ──────────────────────────────────────

def test_very_short_session_produces_near_zero_dose():
    dose = calculate_stress_dose(_log(duration_minutes=0.1, session_rpe=1.0))
    assert dose.d_met_systemic < 5.0
    assert dose.d_nm_central < 5.0
    assert dose.d_struct_damage < 5.0


# ── Human-factor penalties ────────────────────────────────────────────────────

def test_poor_sleep_increases_metabolic_dose():
    good = calculate_stress_dose(_log(sleep_quality=9.0))
    poor = calculate_stress_dose(_log(sleep_quality=2.0))
    assert poor.d_met_systemic > good.d_met_systemic, (
        "Poor sleep should increase metabolic stress dose (less recovery capacity)"
    )
