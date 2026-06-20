# app/models/athlete_state.py
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class AthleteState(Base):
    """
    Persistent history of the Unified State Vector S(t).
    One user can have many AthleteState records over time.
    """
    __tablename__ = "athlete_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    # Capacities
    c_met_aerobic: Mapped[float] = mapped_column(Float, nullable=False)
    c_nm_force: Mapped[float] = mapped_column(Float, nullable=False)
    c_struct: Mapped[float] = mapped_column(Float, nullable=False)

    # Batteries
    b_met_anaerobic: Mapped[float] = mapped_column(Float, nullable=False)

    # Fatigues
    f_met_systemic: Mapped[float] = mapped_column(Float, default=0.0)
    f_nm_peripheral: Mapped[float] = mapped_column(Float, default=0.0)
    f_nm_central: Mapped[float] = mapped_column(Float, default=0.0)
    f_struct_damage: Mapped[float] = mapped_column(Float, default=0.0)

    # Signals & human factors
    s_struct_signal: Mapped[float] = mapped_column(Float, default=0.0)
    habit_strength: Mapped[float] = mapped_column(Float, default=0.0)
    skill_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    engine_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Relationship - points back to User
    user: Mapped["User"] = relationship(
        "User",
        back_populates="athlete_states",
        uselist=False,
    )
    recent_damage: float = Field(default=0.0, description="Rolling structural damage for signal moderation")
