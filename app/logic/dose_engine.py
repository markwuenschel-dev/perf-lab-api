from app.schemas.workouts import WorkoutLog, StressDose


def calculate_stress_dose(log: WorkoutLog) -> StressDose:
    """
    Converts a raw workout log into the Stress Dose Vector D(t).

    Implements the 'Sensors' concept:
      - Modality-specific formulas for Running vs Lifting.
      - 'Global Gain' multiplier based on Sleep/Life Stress.
      - Separates Signal vs Fatigue: human-factor gain affects fatigue, not signal.
    """
    dose = StressDose()

    # --- 1. Base Calculation (Modality Specific) ---

    if log.modality == "Running":
        # Metabolic: Duration * intensity proxy (simple TRIMP-like)
        intensity = log.session_rpe / 10.0
        dose.d_met_systemic = log.duration_minutes * intensity * 1.5

        # Structural: Impact scaling
        dose.d_struct_damage = log.duration_minutes * 0.8 * intensity

        # NM Central: High cost only at higher RPE
        if log.session_rpe >= 9:
            dose.d_nm_central = 30.0
        elif log.session_rpe >= 7:
            dose.d_nm_central = 15.0
        else:
            dose.d_nm_central = 5.0

        # NM Peripheral: Lower for steady running vs lifting
        dose.d_nm_peripheral = log.duration_minutes * intensity * 0.5

    elif log.modality in ["Strength", "Hypertrophy", "Power", "Mixed"]:
        intensity_factor = log.session_rpe / 10.0

        # NM Central: Non-linear scaling with intensity (heavy lifting taxes CNS)
        dose.d_nm_central = (log.duration_minutes * 0.5) * (intensity_factor ** 2) * 5.0

        # NM Peripheral: Volume * intensity
        dose.d_nm_peripheral = log.duration_minutes * intensity_factor * 2.0

        # Structural Signal (Hypertrophy Trigger) via Effective Reps (RIR)
        if log.avg_rir is not None and log.avg_rir <= 3:
            dose.d_struct_signal = log.duration_minutes * 1.5
            dose.d_struct_damage = log.duration_minutes * 1.2
        else:
            dose.d_struct_signal = log.duration_minutes * 0.2
            dose.d_struct_damage = log.duration_minutes * 0.8

        # Metabolic cost of lifting
        dose.d_met_systemic = log.duration_minutes * intensity_factor

    else:
        # Fallback: treat as mild metabolic + structural
        intensity = log.session_rpe / 10.0
        dose.d_met_systemic = log.duration_minutes * intensity
        dose.d_struct_damage = log.duration_minutes * 0.5 * intensity

    # --- 2. The "Human Factor" Global Gain ---

    # 1–10 scale with 5 = neutral baseline.
    # Values below 5 amplify fatigue; above 5 are neutral in this MVP.
    sleep_penalty = 1.0 + max(0.0, (5.0 - log.sleep_quality) * 0.2)
    life_penalty = 1.0 + max(0.0, (5.0 - log.life_stress_inverse) * 0.2)

    global_gain = sleep_penalty * life_penalty

    # Apply global gain to fatigue-like components only, not signal.
    dose.d_met_systemic *= global_gain
    dose.d_nm_central *= global_gain
    dose.d_nm_peripheral *= global_gain
    dose.d_struct_damage *= global_gain

    # Ensure non-negativity
    dose.d_met_systemic = max(0.0, dose.d_met_systemic)
    dose.d_nm_central = max(0.0, dose.d_nm_central)
    dose.d_nm_peripheral = max(0.0, dose.d_nm_peripheral)
    dose.d_struct_damage = max(0.0, dose.d_struct_damage)
    dose.d_struct_signal = max(0.0, dose.d_struct_signal)

    return dose
