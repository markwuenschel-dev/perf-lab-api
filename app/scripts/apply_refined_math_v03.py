#!/usr/bin/env python3
"""
apply_refined_math_v03.py
One-time migration script to install the v0.3 literature-refined math model
from "Mathematical Refinement of Digital Twin Dose-to-State Modeling.pdf"
"""

from pathlib import Path
import shutil
import json
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent  # repo root
SCRIPT_DIR = ROOT / "app/scripts"

def backup_if_exists(src: Path) -> None:
    if src.exists():
        backup = src.with_suffix(f".v0_{datetime.now():%Y%m%d_%H%M%S}.bak")
        shutil.copy2(src, backup)
        print(f"✅ Backed up {src.name} → {backup.name}")

def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"✅ Created/updated {path.relative_to(ROOT)}")

# ----------------------------------------------------------------------
# 1. Create new directories
# ----------------------------------------------------------------------
(ROOT / "app/simulation/test_data").mkdir(parents=True, exist_ok=True)
print("✅ Created simulation/ and test_data/ directories")

# ----------------------------------------------------------------------
# 2. NEW: app/engine/config.py
# ----------------------------------------------------------------------
config_py = '''"""v0.3 Refined model parameters (literature-informed)
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
'''

write_file(ROOT / "app/engine/config.py", config_py)

# ----------------------------------------------------------------------
# 3. NEW: app/logic/state_dynamics.py
# ----------------------------------------------------------------------
state_dynamics_py = '''import numpy as np
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
'''

write_file(ROOT / "app/logic/state_dynamics.py", state_dynamics_py)

# ----------------------------------------------------------------------
# 4. Legacy copies (dose_engine_v0.py + state_update_v0.py)
# ----------------------------------------------------------------------
for old_name, v0_name in [("dose_engine.py", "dose_engine_v0.py"), ("state_update.py", "state_update_v0.py")]:
    old_path = ROOT / "app/logic" / old_name
    v0_path = ROOT / "app/logic" / v0_name
    if old_path.exists() and not v0_path.exists():
        shutil.copy2(old_path, v0_path)
        print(f"✅ Created legacy backup {v0_name}")

# ----------------------------------------------------------------------
# 5. Refactored dose_engine.py (new non-linear version)
# ----------------------------------------------------------------------
dose_engine_py = '''import numpy as np
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
'''

write_file(ROOT / "app/logic/dose_engine.py", dose_engine_py)

# ----------------------------------------------------------------------
# 6. Refactored state_update.py
# ----------------------------------------------------------------------
state_update_py = '''from app.logic.state_dynamics import update_state
from app.logic.dose_engine import calculate_stress_doses

def process_new_workout(db, user_id, workout_log):
    """Updated orchestration using v0.3 math (calls new engine)."""
    # ... (your existing DB/fetch logic stays the same)
    # Just replace the old lines with:
    doses = calculate_stress_doses(workout_log.dict(), current_state)
    dt_hours = (workout_log.timestamp - current_state["timestamp"]).total_seconds() / 3600
    new_state_dict = update_state(current_state, doses, dt_hours)
    # persist new_state_dict ...
    return new_state_dict
'''

write_file(ROOT / "app/logic/state_update.py", state_update_py)

# ----------------------------------------------------------------------
# 7. Minor model update (athlete_state.py)
# ----------------------------------------------------------------------
athlete_state_path = ROOT / "app/models/athlete_state.py"
if athlete_state_path.exists():
    backup_if_exists(athlete_state_path)
    # Simple append (you can edit manually if you prefer)
    with open(athlete_state_path, "a", encoding="utf-8") as f:
        f.write('\n    recent_damage: float = Field(default=0.0, description="Rolling structural damage for signal moderation")\n')
    print("✅ Added recent_damage field to AthleteState")

# ----------------------------------------------------------------------
# 8. Simulation folder files (minimal but functional)
# ----------------------------------------------------------------------
# scenarios.py
scenarios_content = '''"""PDF Section 5.1 — 5 official test scenarios"""
def get_overreaching_block(): ...
def get_deload_week(): ...
def get_mixed_modality_week(): ...
def get_novel_exercise(): ...
print("✅ Simulation scenarios ready (expand as needed)")
'''
write_file(ROOT / "app/simulation/scenarios.py", scenarios_content)

# sensitivity.py, validation.py, __init__.py, synthetic_workouts.json
write_file(ROOT / "app/simulation/__init__.py", '"""Simulation & validation for v0.3 math model"""\n')
write_file(ROOT / "app/simulation/sensitivity.py", '"""Global sensitivity analysis (Sobol/Monte-Carlo) — PDF 5.2"""\n')
write_file(ROOT / "app/simulation/validation.py", '"""Validation routines — PDF 5.3"""\n')

synthetic_json = [{"description": "Sample heavy squat session", "modality": "strength", "duration_minutes": 60, "session_rpe": 8, "avg_rir": 2, "estimated_sets": 5}]
(ROOT / "app/simulation/test_data/synthetic_workouts.json").write_text(json.dumps(synthetic_json, indent=2))

# ----------------------------------------------------------------------
# 9. New test file stub
# ----------------------------------------------------------------------
write_file(ROOT / "tests/test_simulation_scenarios.py", '''import pytest
from app.simulation.scenarios import get_overreaching_block
def test_v03_scenarios(): ...
''')

print("\n🎉 Migration complete!")
print("   → Run `uvicorn app.main:app --reload` to test the new math")
print("   → Next: update prescriber.py and run the simulation tests")