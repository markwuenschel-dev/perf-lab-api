# app/models/athlete_state.py
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class AthleteState(Base):
    """
    Persistent history of the Unified State Vector S(t).
    One user can have many AthleteState records over time.
    """
    __tablename__ = "athlete_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Capacities
    c_met_aerobic = Column(Float, nullable=False)
    c_nm_force = Column(Float, nullable=False)
    c_struct = Column(Float, nullable=False)

    # Batteries
    b_met_anaerobic = Column(Float, nullable=False)

    # Fatigues
    f_met_systemic = Column(Float, default=0.0)
    f_nm_peripheral = Column(Float, default=0.0)
    f_nm_central = Column(Float, default=0.0)
    f_struct_damage = Column(Float, default=0.0)

    # Signals & human factors
    s_struct_signal = Column(Float, default=0.0)
    habit_strength = Column(Float, default=0.0)
    skill_state = Column(JSONB, default=dict)
    engine_state = Column(JSONB, nullable=True)

    # Relationship - points back to User
    user: Mapped["User"] = relationship(
        "User",
        back_populates="athlete_states",
        uselist=False,
    )
    recent_damage: float = Field(default=0.0, description="Rolling structural damage for signal moderation")
