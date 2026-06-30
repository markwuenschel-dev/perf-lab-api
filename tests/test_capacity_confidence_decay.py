from __future__ import annotations
from datetime import UTC, datetime, timedelta

from app.engine.parameters import EngineParameters, default_parameters
from app.engine.feature_flags import ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution, CapacityConfidence, CapacityState,
    FatigueState, StressDoseSix, TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(conf: float = 1.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    cap_conf = CapacityConfidence(
        aerobic=conf, glycolytic=conf, max_strength=conf,
        hypertrophy=conf, power=conf, skill=conf,
        mobility=conf, work_capacity=conf,
    )
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        capacity_confidence=cap_conf,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength",
        duration_minutes=60.0, session_rpe=6.0,
        sleep_quality=7.0, life_stress_inverse=7.0,
    )


def _zero_dose() -> StressDose:
    return StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())


def test_variance_increases_with_time():
    s0 = _state(conf=0.20)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=7), _log())
    assert s1.capacity_confidence.max_strength > s0.capacity_confidence.max_strength
    assert s1.capacity_confidence.aerobic > s0.capacity_confidence.aerobic


def test_variance_is_capped_per_axis():
    p = default_parameters()
    s0 = _state(conf=1.0)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=365), _log())
    for key in ("aerobic", "max_strength", "power", "skill"):
        v = getattr(s1.capacity_confidence, key)
        max_v = p.confidence_max_variance.get(key, 1.5)
        assert v <= max_v, f"{key}: {v} exceeds max {max_v}"


def test_different_axes_have_different_noise_rates():
    p = default_parameters()
    q_power = p.confidence_process_noise_per_day.get("power", 0.0)
    q_mobility = p.confidence_process_noise_per_day.get("mobility", 0.0)
    assert q_power > q_mobility, "Power should decay confidence faster than mobility"


def test_workout_logs_do_not_reduce_variance():
    """Workout training maintains fitness but not observability — only benchmarks reduce variance."""
    assert ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE is False, \
        "Feature flag must remain False — workout-informed maintenance not yet validated"
    s0 = _state(conf=0.50)
    # Even many training sessions should not pull variance down
    s = s0
    dose = StressDose(
        dose_six=StressDoseSix(volume=1.0, intensity=0.8, density=0.5, impact=0.3, skill=0.2, metabolic=0.4),
        adaptation_contribution=AdaptationContribution(max_strength=3.0),
        d_nm_central=3.0, d_nm_peripheral=2.0, d_met_systemic=1.0, d_struct_damage=0.5, d_struct_signal=2.0,
    )
    for _ in range(12):  # 12 sessions
        s = update_athlete_state(s, dose, timedelta(days=2), _log())
    # Variance should not have decreased from training alone
    assert s.capacity_confidence.max_strength >= 0.50, \
        "Training logs must not reduce capacity confidence — only benchmarks may do so"
