from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Prefer domain layer as source of truth (schemas re-export for compatibility)
from app.domain.vectors import CapacityState, FatigueState, TissueState


class UnifiedStateVector(BaseModel):
    """
    Digital twin snapshot S(t): legacy scalars + decomposed engine vectors.

    Legacy fields remain for backward-compatible API clients; they are derived
    from (capacity_x, fatigue_f, tissue_t) on each update.
    """

    timestamp: datetime

    capacity_x: CapacityState = Field(default_factory=CapacityState)
    fatigue_f: FatigueState = Field(default_factory=FatigueState)
    tissue_t: TissueState = Field(default_factory=TissueState)

    # Legacy capacities (mirrors of X / batteries)
    c_met_aerobic: float = Field(..., description="Aerobic capacity (e.g. CS / VO2 proxy)")
    c_nm_force: float = Field(..., description="Maximal strength / force capacity")
    c_struct: float = Field(..., description="Structural capacity / CSA proxy")
    b_met_anaerobic: float = Field(..., description="Anaerobic work capacity (W'/D')")

    # Legacy fatigues (mirrors of F aggregate view)
    f_met_systemic: float = Field(0.0, ge=0.0, le=100.0)
    f_nm_peripheral: float = Field(0.0, ge=0.0, le=100.0)
    f_nm_central: float = Field(0.0, ge=0.0, le=100.0)
    f_struct_damage: float = Field(0.0, ge=0.0, le=100.0)

    s_struct_signal: float = Field(0.0, ge=0.0)

    habit_strength: float = Field(0.0, ge=0.0, le=1.0)
    skill_state: dict[str, float] = Field(default_factory=dict)
    model_version: str = Field("v0.3", description="State engine version")

    model_config = ConfigDict(from_attributes=True)
