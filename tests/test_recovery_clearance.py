"""
Tests for the multiplicative recovery clearance modifier.

Verifies:
- Good recovery (high sleep/low stress) yields multiplier > 1  (faster clearance).
- Poor recovery (low sleep/high stress) yields multiplier < 1  (slower clearance).
- Multiplier is bounded to [recovery_clearance_min, recovery_clearance_max].
- Neutral inputs (sleep=7, stress=7) give multiplier ≈ 1.0.
- A dose with real training values still raises fatigue above the pure-decay floor.
- life_stress_inverse direction: high stress → slower clearance, low stress → faster.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.engine.parameters import default_parameters
from app.logic.state_update_v0 import recovery_clearance_multiplier, update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(cns: float = 30.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=cns, metabolic=cns, structural=cns, tendon=cns, grip=cns)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.0,
        skill_state={},
        **leg,
    )


def _log(sleep: float = 7.0, stress: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC),
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=6.0,
        sleep_quality=sleep,
        life_stress_inverse=stress,
    )



def test_good_sleep_clears_fatigue_faster_than_neutral():
    p = default_parameters()
    m_good = recovery_clearance_multiplier("cns", 9.0, 7.0, p)
    m_neutral = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert m_good > m_neutral, f"Good sleep ({m_good:.3f}) must exceed neutral ({m_neutral:.3f})"


def test_poor_sleep_clears_fatigue_slower_than_neutral():
    p = default_parameters()
    m_poor = recovery_clearance_multiplier("cns", 3.0, 7.0, p)
    m_neutral = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert m_poor < m_neutral, f"Poor sleep ({m_poor:.3f}) must be below neutral ({m_neutral:.3f})"


def test_poor_sleep_never_below_clearance_min():
    p = default_parameters()
    for axis in ("cns", "muscular", "metabolic", "structural", "tendon", "grip"):
        m = recovery_clearance_multiplier(axis, 1.0, 1.0, p)
        assert m >= p.recovery_clearance_min, (
            f"Axis {axis}: {m:.3f} below min {p.recovery_clearance_min}"
        )


def test_multiplier_bounded():
    p = default_parameters()
    m_high = recovery_clearance_multiplier("cns", 10.0, 10.0, p)
    m_low = recovery_clearance_multiplier("cns", 1.0, 1.0, p)
    assert m_high <= p.recovery_clearance_max
    assert m_low >= p.recovery_clearance_min


def test_neutral_inputs_give_multiplier_near_one():
    p = default_parameters()
    m = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert 0.98 <= m <= 1.02, f"Neutral inputs should give ~1.0, got {m:.3f}"


def test_dose_impulse_still_increases_fatigue_after_decay():
    p = default_parameters()
    s0 = _state(cns=10.0)
    dose = StressDose(
        dose_six=StressDoseSix(volume=1.0, intensity=1.0, density=0.5, impact=0.5, skill=0.5, metabolic=0.5),
        adaptation_contribution=AdaptationContribution(),
        d_nm_central=5.0,
        d_nm_peripheral=3.0,
        d_met_systemic=2.0,
        d_struct_damage=1.0,
    )
    s1 = update_athlete_state(s0, dose, timedelta(hours=1), _log())
    decayed_floor = s0.fatigue_f.cns * math.exp(-1.0 / p.tau_fatigue_hours["cns"])
    assert s1.fatigue_f.cns > decayed_floor, (
        f"Dose must raise CNS fatigue above pure-decay baseline "
        f"(got {s1.fatigue_f.cns:.4f}, floor {decayed_floor:.4f})"
    )


def test_life_stress_inverse_direction():
    """High life_stress_inverse (low real stress) → faster clearance; low → slower."""
    p = default_parameters()
    m_high_stress = recovery_clearance_multiplier("cns", 7.0, 3.0, p)
    m_low_stress = recovery_clearance_multiplier("cns", 7.0, 9.0, p)
    assert m_high_stress < 1.0, (
        f"High life stress (inverse=3.0) should yield multiplier < 1.0, got {m_high_stress:.3f}"
    )
    assert m_low_stress > 1.0, (
        f"Low life stress (inverse=9.0) should yield multiplier > 1.0, got {m_low_stress:.3f}"
    )
