"""Engine feature flags.

All flags default to False. Do not enable in production without validation data.
"""

ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE: bool = False
ENABLE_TISSUE_RISK_CANDIDATE_PENALTY: bool = False
ENABLE_DECREMENT_PREDICTION_HARD_BLOCK: bool = False
# ADR-0042: let the MPC planner influence prescriptions (promotion gate). While False the
# planner runs shadow-only — it logs MPC-vs-greedy but never changes what is prescribed.
ENABLE_MPC_PRESCRIPTION: bool = False
