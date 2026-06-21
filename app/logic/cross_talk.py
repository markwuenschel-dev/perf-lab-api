# Time constants in hours (Based on 'Multi-Timescale Sensitivity')
TAU_FATIGUE_FAST = 48.0   # Peripheral/Glycogen (2 days)
TAU_FATIGUE_SLOW = 360.0  # Systemic/CNS (15 days)
TAU_DAMAGE = 96.0         # Structural DOMS (4 days)
TAU_SIGNAL = 24.0         # mTOR signaling window (1 day)

# Concurrent-training interference coefficients (ADR-0037). Applied as suppression
# of strength/power adaptation efficiency, scaled by a [0,1] fatigue fraction:
#   factor = 1 - COEF * fatigue01   (floored, see state_update_v0._interference_factor)
# Endurance/metabolic load blunts strength gains; structural damage suppresses power.
INTERFERENCE_MET_ON_FORCE = 1.3
INTERFERENCE_DAM_ON_POWER = 0.6
