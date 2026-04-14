"""v0.3 Refined model parameters (literature-informed)
See: Mathematical Refinement of Digital Twin Dose-to-State Modeling.pdf
"""
from typing import Dict, Any

# DOSE MAPPING PARAMETERS (PDF Section 4.1 + Table 1)
DOSE_PARAMS: Dict[str, float] = {
    "alpha_met": 0.05,
    "beta_rir": 0.5,
    "gamma_peripheral": 0.001,
    "delta_central": 0.1,
    "epsilon_damage": 0.01,
    "zeta_signal": 0.2,
    "eta_history_damage": 0.1,
}

# TIME CONSTANTS (hours) — PDF Section 4.3
TIME_CONSTANTS: Dict[str, float] = {
    "tau_met": 12.0,
    "tau_nm_p": 36.0,
    "tau_nm_c": 24.0,
    "tau_struct": 48.0,
    "tau_signal": 72.0,
    "tau_batt": 0.5,
}

# CROSS-TALK MATRIX (PDF Section 4.2)
CROSS_TALK_MATRIX = [
    [0.7, 0.0, 0.0, 0.2, 0.0],
    [0.0, 0.8, 0.1, 0.0, 0.0],
    [0.3, 0.0, 0.6, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.9, 0.5],
    [0.0, 0.0, 0.0, 0.0, 0.5],
]

# Modality weights
MODALITY_WEIGHTS: Dict[str, list[float]] = {
    "endurance": [1.0, 0.1, 0.7, 0.1, 0.1],
    "strength":  [0.2, 0.9, 0.3, 0.8, 0.7],
    "hypertrophy": [0.3, 1.0, 0.4, 1.0, 0.9],
    "power":     [0.4, 0.8, 1.0, 0.5, 0.6],
    "mixed":     [0.8, 0.7, 0.7, 0.6, 0.5],
}

MODEL_VERSION = "0.3-refined"
