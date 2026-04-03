"""
Session stress dose D(t): six-dimensional vector + legacy fatigue channels.

Uses parameter exponents from EngineParameters and modality φ defaults.
"""

from __future__ import annotations

import math

from app.engine.parameters import default_parameters
from app.engine.phi_table import default_phi_for_row
from app.schemas.engine_vectors import StressDoseSix
from app.schemas.workouts import StressDose, WorkoutLog


def _infer_movement_pattern(log: WorkoutLog) -> str:
    if log.dominant_movement_pattern:
        return log.dominant_movement_pattern
    if log.modality == "Running":
        return "run"
    return "mixed"


def calculate_stress_dose(log: WorkoutLog) -> StressDose:
    p = default_parameters()
    movement = _infer_movement_pattern(log)

    # Volume proxy V
    sets = log.estimated_sets or max(3.0, log.duration_minutes / 12.0)
    vol_load = log.total_volume_load or 0.0
    V = log.duration_minutes + 0.02 * vol_load + 2.0 * sets

    intensity_u = log.session_rpe / 10.0
    density_raw = min(2.5, log.duration_minutes / max(20.0, sets * 5.0))
    Delta = max(0.35, density_raw)
    N = max(0.2, log.novelty)
    if log.avg_rir is not None:
        F = max(0.15, min(1.0, (10.0 - log.avg_rir) / 10.0))
    else:
        F = max(0.2, intensity_u)

    phi_pack = default_phi_for_row(
        log.modality,
        movement,
        skill_demand=0.55 if log.modality in ("Power", "Mixed") else 0.45,
        impact_level=0.75 if log.modality == "Running" else 0.55,
    )
    phi_f = phi_pack["phi_fatigue"]
    w_phi = max(0.25, sum(phi_f.values()) / max(1, len(phi_f)))

    base = (
        w_phi
        * math.log1p(V)
        * (intensity_u**p.dose_alpha)
        * (Delta**p.dose_beta)
        * (N**p.dose_gamma)
        * (F**p.dose_rho)
    )

    if log.modality == "Running":
        em = phi_pack["energy_mix"]
        six = StressDoseSix(
            volume=base * 0.35 * (em["aerobic"] + 0.4),
            intensity=base * 0.45 * intensity_u,
            density=base * 0.25 * Delta,
            impact=base * 0.55 * max(intensity_u, 0.4),
            skill=base * 0.08,
            metabolic=base * 0.9 * (em["aerobic"] + em["glycolytic"]),
        )
    elif log.modality in ("Strength", "Hypertrophy", "Power", "Mixed"):
        six = StressDoseSix(
            volume=base * 0.5,
            intensity=base * 0.65 * intensity_u,
            density=base * 0.35 * Delta,
            impact=base * 0.25 * F,
            skill=base * 0.2 * phi_pack["phi_adapt"].get("skill", 0.2),
            metabolic=base * 0.35 * (phi_pack["energy_mix"]["glycolytic"] + 0.2),
        )
    else:
        six = StressDoseSix(
            volume=base * 0.4,
            intensity=base * 0.5 * intensity_u,
            density=base * 0.3 * Delta,
            impact=base * 0.3,
            skill=base * 0.15,
            metabolic=base * 0.45,
        )

    sleep_penalty = 1.0 + max(0.0, (5.0 - log.sleep_quality) * 0.2)
    life_penalty = 1.0 + max(0.0, (5.0 - log.life_stress_inverse) * 0.2)
    global_gain = sleep_penalty * life_penalty
    six = six.scaled(global_gain)

    # Legacy channels (same human-factor gain on fatigue-like terms)
    d_met_systemic = six.metabolic * 14.0 + six.density * 6.0
    d_nm_peripheral = six.volume * 0.55 + six.intensity * 9.0
    d_nm_central = six.intensity * 11.0 + six.skill * 7.0
    d_struct_damage = six.impact * 18.0 + six.volume * 0.35
    if log.avg_rir is not None and log.avg_rir <= 3:
        d_struct_signal = log.duration_minutes * 1.5 + six.intensity * 4.0
    else:
        d_struct_signal = log.duration_minutes * 0.25 + six.intensity * 1.0

    return StressDose(
        dose_six=six,
        d_met_systemic=max(0.0, d_met_systemic),
        d_nm_peripheral=max(0.0, d_nm_peripheral),
        d_nm_central=max(0.0, d_nm_central),
        d_struct_damage=max(0.0, d_struct_damage),
        d_struct_signal=max(0.0, d_struct_signal),
    )
