import math
from datetime import timedelta

from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose
from app.logic import cross_talk


def _exp_decay(value: float, hours: float, tau: float) -> float:
    """
    Exponential decay: N(t) = N0 * e^(-t / tau).
    Used for fatigue and signaling decay.
    """
    if value <= 0.01:
        return 0.0
    return value * math.exp(-hours / tau)


def update_athlete_state(
    prev_state: UnifiedStateVector,
    dose: StressDose,
    time_delta: timedelta,
) -> UnifiedStateVector:
    """
    Evolves the state vector S(t) to S(t+1) via:

      S(t+1) = Decay(S(t)) + D(t) + (slow adaptations on capacities)
    """
    hours = time_delta.total_seconds() / 3600.0
    if hours < 0:
        hours = 0.0

    s = prev_state.model_copy(deep=True)

    # 1. Decay (Recovery) of fatigues and signaling
    s.f_met_systemic = _exp_decay(s.f_met_systemic, hours, cross_talk.TAU_FATIGUE_SLOW)
    s.f_nm_peripheral = _exp_decay(s.f_nm_peripheral, hours, cross_talk.TAU_FATIGUE_FAST)
    s.f_nm_central = _exp_decay(s.f_nm_central, hours, cross_talk.TAU_FATIGUE_SLOW)
    s.f_struct_damage = _exp_decay(s.f_struct_damage, hours, cross_talk.TAU_DAMAGE)
    s.s_struct_signal = _exp_decay(s.s_struct_signal, hours, cross_talk.TAU_SIGNAL)

    # 2. Add the Stress Dose (Impulse)
    s.f_met_systemic += dose.d_met_systemic
    s.f_nm_peripheral += dose.d_nm_peripheral
    s.f_nm_central += dose.d_nm_central
    s.f_struct_damage += dose.d_struct_damage
    s.s_struct_signal += dose.d_struct_signal

    # 3. Simple adaptation rule (structural capacity)
    # If enough hypertrophy signal has accumulated, bump c_struct slightly.
    if s.s_struct_signal > 20.0:
        s.c_struct += 0.01

    # 4. Safety clamping
    s.f_met_systemic = max(0.0, min(100.0, s.f_met_systemic))
    s.f_nm_peripheral = max(0.0, min(100.0, s.f_nm_peripheral))
    s.f_nm_central = max(0.0, min(100.0, s.f_nm_central))
    s.f_struct_damage = max(0.0, min(100.0, s.f_struct_damage))
    s.s_struct_signal = max(0.0, s.s_struct_signal)

    # 5. Advance timestamp
    s.timestamp = prev_state.timestamp + time_delta

    return s
