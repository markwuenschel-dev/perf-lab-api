"""Engine feature flags.

All flags default to False. Do not enable in production without validation data.
"""

ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE: bool = False
ENABLE_TISSUE_RISK_CANDIDATE_PENALTY: bool = False
ENABLE_DECREMENT_PREDICTION_HARD_BLOCK: bool = False
