# Time constants in hours (Based on 'Multi-Timescale Sensitivity')
TAU_FATIGUE_FAST = 48.0   # Peripheral/Glycogen (2 days)
TAU_FATIGUE_SLOW = 360.0  # Systemic/CNS (15 days)
TAU_DAMAGE = 96.0         # Structural DOMS (4 days)
TAU_SIGNAL = 24.0         # mTOR signaling window (1 day)

# Cross-talk interaction coefficients (for future use in suppression models)
INTERFERENCE_MET_ON_FORCE = 0.05
INTERFERENCE_DAM_ON_POWER = 0.10
