# Time constants in hours (Based on 'Multi-Timescale Sensitivity')
TAU_FATIGUE_FAST = 48.0   # Peripheral/Glycogen (2 days)
TAU_FATIGUE_SLOW = 360.0  # Systemic/CNS (15 days)
TAU_DAMAGE = 96.0         # Structural DOMS (4 days)
TAU_SIGNAL = 24.0         # mTOR signaling window (1 day)

# Concurrent-training interference (ADR-0037) now lives entirely in
# app/logic/interference.py (exponential per-axis suppression, the single
# authority). The former legacy-linear coefficients here — INTERFERENCE_MET_ON_FORCE
# and INTERFERENCE_DAM_ON_POWER — were removed when the structural bump was routed
# through directional_interference_multiplier.
