import numpy as np
from app.engine.config import DOSE_PARAMS, MODALITY_WEIGHTS

def calculate_stress_doses(workout: dict, athlete_state: dict) -> dict:
    """Non-linear dose mapping (PDF Section 4.1)."""
    T = workout["duration_minutes"]
    RPE = workout["session_rpe"]
    RIR = workout.get("avg_rir", 3.0)
    VL = workout.get("total_volume_load", 0.0)
    S = workout.get("sleep_quality", 0.8)
    LS = workout.get("life_stress_inverse", 0.8)
    N = workout.get("novelty", 0.0)
    sets = workout.get("estimated_sets", 3)
    modality = workout.get("modality", "mixed")
    pattern = workout.get("dominant_movement_pattern", "")

    w = MODALITY_WEIGHTS.get(modality, MODALITY_WEIGHTS["mixed"])

    d_met = (DOSE_PARAMS["alpha_met"] * T * (RPE/10)**2 * np.exp(-DOSE_PARAMS["beta_rir"]*RIR)) * w[0] * S * LS
    d_nm_p = (DOSE_PARAMS["gamma_peripheral"] * sets * (1 + 1/(RIR+1)) * VL) * w[1] * S * LS
    d_nm_c = (DOSE_PARAMS["delta_central"] * np.sqrt(T) * (RPE/10)**1.5) * w[2] * (1-N) * S * LS

    ecc = 1.5 if pattern in ["deadlift","clean"] else 1.2 if pattern in ["squat","lunge"] else 1.0
    d_struct_d = (DOSE_PARAMS["epsilon_damage"] * sets * (1 - RIR/5)**2 * ecc) * w[3] * S * LS

    history_damage = athlete_state.get("recent_damage", 0.0)
    d_struct_s = DOSE_PARAMS["zeta_signal"] * d_struct_d * np.exp(-DOSE_PARAMS["eta_history_damage"]*history_damage) * w[4]

    return {
        "d_met_systemic": float(d_met),
        "d_nm_peripheral": float(d_nm_p),
        "d_nm_central": float(d_nm_c),
        "d_struct_damage": float(d_struct_d),
        "d_struct_signal": float(d_struct_s),
    }


# LEGACY: This module uses the numpy-based dose engine with signature:
#   calculate_stress_doses(workout: dict, athlete_state: dict) -> dict
# For the production v0.3 engine (WorkoutLog → StressDose), import from:
#   app.logic.dose_engine_v0.calculate_stress_dose
# The alias below is intentionally kept for any script that already imports it,
# but it does NOT have the same signature as dose_engine_v0.calculate_stress_dose.
calculate_stress_dose = calculate_stress_doses
