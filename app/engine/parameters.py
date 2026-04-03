"""
Literature-oriented parameter tables (Banister-style decays, Foster-like load exponents).

Tunable constants; future: load from DB or YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class EngineParameters:
    # Dose law: D_k = w_k * log(1+V) * I^α * Δ^β * N^γ * F^ρ
    dose_alpha: float = 1.2  # intensity exponent
    dose_beta: float = 0.9  # density exponent
    dose_gamma: float = 0.6  # novelty exponent
    dose_rho: float = 1.0  # proximity-to-failure exponent

    # Fatigue decay Λ — half-life style hours (approx exp decay tau in cross_talk)
    tau_fatigue_hours: Dict[str, float] = field(
        default_factory=lambda: {
            "cns": 360.0,  # slow (~15d)
            "muscular": 72.0,  # medium (~3d)
            "metabolic": 24.0,  # fast
            "structural": 720.0,  # very slow (~30d)
            "tendon": 960.0,
            "grip": 48.0,
        }
    )

    # Tissue decay Γ (same interpretation as fatigue — hours to decay stress)
    tau_tissue_hours: Dict[str, float] = field(
        default_factory=lambda: {
            "shoulder": 168.0,
            "elbow": 168.0,
            "wrist": 120.0,
            "lumbar": 240.0,
            "hip": 168.0,
            "knee": 168.0,
            "ankle": 144.0,
            "finger": 96.0,
        }
    )

    # Recovery Ω gain from sleep / stress (multiplier on fatigue clearance per hour)
    recovery_sleep_scale: float = 0.08
    recovery_stress_scale: float = 0.06

    # Capacity adaptation (very slow Banister-style bump from hypertrophy signal)
    capacity_signal_threshold: float = 20.0
    capacity_struct_bump: float = 0.02
    capacity_hypertrophy_bump: float = 0.03

    # Cross-talk G on capacity (small off-diagonal nudges)
    crosstalk_metabolic_on_work_capacity: float = 0.02


def default_parameters() -> EngineParameters:
    return EngineParameters()
