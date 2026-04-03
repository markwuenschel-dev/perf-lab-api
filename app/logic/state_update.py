"""
State evolution S(t) → S(t+1): multi-component fatigue, tissue load, slow capacity,
and legacy scalar sync.
"""

from __future__ import annotations

import math
from datetime import timedelta

from app.engine.parameters import default_parameters
from app.engine.phi_table import default_phi_for_row
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import cross_talk
from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


def _exp_decay(value: float, hours: float, tau: float) -> float:
    if value <= 0.01:
        return 0.0
    return value * math.exp(-hours / max(1e-6, tau))


def _fatigue_impulse_from_dose(dose: StressDose) -> FatigueState:
    """Map legacy + six-vector dose into F increments (aligned with prior magnitudes)."""
    six = dose.dose_six
    return FatigueState(
        cns=min(100.0, dose.d_nm_central * 0.78 + six.intensity * 2.5 + six.skill * 2.0),
        muscular=min(100.0, dose.d_nm_peripheral * 0.88 + six.volume * 0.5),
        metabolic=min(100.0, dose.d_met_systemic * 0.92 + six.metabolic * 2.0),
        structural=min(100.0, dose.d_struct_damage * 0.52 + six.impact * 3.5),
        tendon=min(100.0, dose.d_struct_damage * 0.38 + six.impact * 2.8),
        grip=min(
            100.0,
            dose.d_struct_damage * 0.22 + dose.d_nm_peripheral * 0.12 + six.intensity * 1.2,
        ),
    )


def _tissue_impulse_from_dose(dose: StressDose, log: WorkoutLog) -> dict[str, float]:
    movement = log.dominant_movement_pattern or (
        "run" if log.modality == "Running" else "mixed"
    )
    phi = default_phi_for_row(
        log.modality,
        movement,
        skill_demand=0.5,
        impact_level=0.65 if log.modality == "Running" else 0.5,
    )
    pt = phi["phi_tissue"]
    six = dose.dose_six
    scale = six.impact * 0.6 + six.volume * 0.04 + six.intensity * 0.45
    return {k: float(pt.get(k, 0.05)) * scale * 9.0 for k in TissueState.KEYS}


def update_athlete_state(
    prev_state: UnifiedStateVector,
    dose: StressDose,
    time_delta: timedelta,
    log: WorkoutLog,
) -> UnifiedStateVector:
    hours = time_delta.total_seconds() / 3600.0
    if hours < 0:
        hours = 0.0

    p = default_parameters()
    s = prev_state.model_copy(deep=True)

    # --- 1. Fatigue decay (Λ) ---
    for key in FatigueState.KEYS:
        tau = p.tau_fatigue_hours[key]
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, _exp_decay(v, hours, tau))

    # --- 2. Tissue decay (Γ) — accumulated stress eases ---
    for key in TissueState.KEYS:
        tau = p.tau_tissue_hours[key]
        v = getattr(s.tissue_t, key)
        setattr(s.tissue_t, key, _exp_decay(v, hours, tau))

    # --- 3. Recovery Ω (sleep / life stress) ---
    omega = (
        p.recovery_sleep_scale * max(0.0, 5.0 - log.sleep_quality) * hours
        + p.recovery_stress_scale * max(0.0, 5.0 - log.life_stress_inverse) * hours
    )
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, max(0.0, v - omega * 0.18))

    # --- 4. Impulses from training dose ---
    d_f = _fatigue_impulse_from_dose(dose)
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key) + getattr(d_f, key)
        setattr(s.fatigue_f, key, max(0.0, min(100.0, v)))

    d_t = _tissue_impulse_from_dose(dose, log)
    for key in TissueState.KEYS:
        v = getattr(s.tissue_t, key) + d_t[key]
        setattr(s.tissue_t, key, max(0.0, min(100.0, v)))

    # --- 5. Signaling + slow capacity (Banister-style nudge) ---
    s.s_struct_signal = _exp_decay(s.s_struct_signal, hours, cross_talk.TAU_SIGNAL)
    s.s_struct_signal += dose.d_struct_signal
    s.s_struct_signal = max(0.0, s.s_struct_signal)

    if s.s_struct_signal > p.capacity_signal_threshold:
        s.capacity_x.hypertrophy = min(
            100.0,
            s.capacity_x.hypertrophy + p.capacity_hypertrophy_bump,
        )
        s.capacity_x.max_strength = min(
            100.0,
            s.capacity_x.max_strength + p.capacity_struct_bump,
        )
        s.s_struct_signal *= 0.85

    wc_gain = p.crosstalk_metabolic_on_work_capacity * min(s.fatigue_f.metabolic * 0.015, 0.4)
    s.capacity_x.work_capacity = min(100.0, s.capacity_x.work_capacity + wc_gain)

    # --- 6. Legacy mirrors ---
    legacy = sync_legacy_from_vectors(s.capacity_x, s.fatigue_f, s.tissue_t)
    s.c_met_aerobic = legacy["c_met_aerobic"]
    s.c_nm_force = legacy["c_nm_force"]
    s.c_struct = legacy["c_struct"]
    s.b_met_anaerobic = legacy["b_met_anaerobic"]
    s.f_met_systemic = legacy["f_met_systemic"]
    s.f_nm_peripheral = legacy["f_nm_peripheral"]
    s.f_nm_central = legacy["f_nm_central"]
    s.f_struct_damage = legacy["f_struct_damage"]

    s.timestamp = prev_state.timestamp + time_delta
    return s
