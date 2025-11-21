from datetime import datetime
from typing import Dict

from pydantic import BaseModel, Field


class UnifiedStateVector(BaseModel):
    """
    DTO for S(t). Represents the digital twin snapshot.
    """
    timestamp: datetime

    # Capacities
    c_met_aerobic: float = Field(..., description="Aerobic capacity (e.g. CS / VO2 proxy)")
    c_nm_force: float = Field(..., description="Maximal strength / force capacity")
    c_struct: float = Field(..., description="Structural capacity / CSA proxy")
    b_met_anaerobic: float = Field(..., description="Anaerobic work capacity (W'/D')")

    # Fatigues (0–100 scales recommended)
    f_met_systemic: float = Field(0.0, ge=0.0, le=100.0)
    f_nm_peripheral: float = Field(0.0, ge=0.0, le=100.0)
    f_nm_central: float = Field(0.0, ge=0.0, le=100.0)
    f_struct_damage: float = Field(0.0, ge=0.0, le=100.0)

    # Signals
    s_struct_signal: float = Field(0.0, ge=0.0)

    # Human Factors
    habit_strength: float = Field(0.0, ge=0.0, le=1.0)
    skill_state: Dict[str, float] = Field(default_factory=dict)

    class Config:
        from_attributes = True
