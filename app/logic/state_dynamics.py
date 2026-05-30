import numpy as np
from app.engine.config import TIME_CONSTANTS, CROSS_TALK_MATRIX

def update_state(prev_state: dict, doses: dict, dt_hours: float) -> dict:
    """Multi-timescale state update with cross-talk (PDF Section 4.3)."""
    F_prev = np.array([
        prev_state["f_met_systemic"],
        prev_state["f_nm_peripheral"],
        prev_state["f_nm_central"],
        prev_state["f_struct_damage"],
        prev_state["s_struct_signal"],
    ])

    F_new = np.dot(CROSS_TALK_MATRIX, F_prev)
    taus = np.array([TIME_CONSTANTS[k] for k in ["tau_met", "tau_nm_p", "tau_nm_c", "tau_struct", "tau_signal"]])
    decay = np.exp(-dt_hours / taus)

    doses_vec = np.array([doses[k] for k in ["d_met_systemic", "d_nm_peripheral", "d_nm_central", "d_struct_damage", "d_struct_signal"]])
    F_new = F_new * decay + doses_vec

    # Capacity adaptation (structural)
    total_fatigue = sum(prev_state.get(f, 0) for f in ["f_met_systemic", "f_nm_peripheral", "f_nm_central", "f_struct_damage"])
    capacity_gain = 0.001 * F_new[4] * np.exp(-0.5 * total_fatigue) * (dt_hours / 24.0)
    c_struct_new = prev_state.get("c_struct", 100.0) + capacity_gain

    # Anaerobic battery
    batt_recharge = (1 - prev_state.get("b_met_anaerobic", 1.0)) / TIME_CONSTANTS["tau_batt"] * dt_hours
    b_new = min(1.0, prev_state.get("b_met_anaerobic", 1.0) + batt_recharge)

    return {
        "f_met_systemic": float(F_new[0]),
        "f_nm_peripheral": float(F_new[1]),
        "f_nm_central": float(F_new[2]),
        "f_struct_damage": float(F_new[3]),
        "s_struct_signal": float(F_new[4]),
        "c_struct": float(c_struct_new),
        "b_met_anaerobic": float(b_new),
    }
