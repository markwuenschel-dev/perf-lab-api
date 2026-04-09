# app/models/athlete_state.py
from datetime import datetime

from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship   # ← ADD THIS

from app.core.db import Base


class AthleteState(Base):
    """
    Persistent history of the Unified State Vector S(t).
    Based on 'Unified Sports Performance Framework' - Table 1.
    """
    __tablename__ = "athlete_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Capacities (The Ceiling - Slow Adaptation)
    c_met_aerobic = Column(Float, nullable=False, comment="Critical Speed/Power")
    c_nm_force = Column(Float, nullable=False, comment="Max Force/1RM")
    c_struct = Column(Float, nullable=False, comment="Structural Integrity/CSA")

    # Batteries (The Tank - Fast Recharge)
    b_met_anaerobic = Column(Float, nullable=False, comment="W' or D'")

    # Fatigues (The Cost - Fast/Mid Decay), stored as arbitrary units (0–100 recommended)
    f_met_systemic = Column(Float, default=0.0)
    f_nm_peripheral = Column(Float, default=0.0)
    f_nm_central = Column(Float, default=0.0)
    f_struct_damage = Column(Float, default=0.0)

    # Signals (The Adaptation Trigger)
    s_struct_signal = Column(Float, default=0.0)

    # Human Factors
    habit_strength = Column(Float, default=0.0)
    skill_state = Column(JSONB, default=dict)  # e.g. {"squat": 0.8}

    # Full-spectrum engine bundle: {"x": CapacityState, "f": FatigueState, "t": TissueState}
    engine_state = Column(JSONB, nullable=True)

    user = relationship(
        "User",
        back_populates="athlete_state",   # must match the name used in User model
        uselist=False,                    # one-to-one
    )