# app/models/user.py
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,  # ← was missing
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    # Imported only for SQLAlchemy Mapped[...] forward refs; the relationship
    # strings are resolved at runtime via SQLAlchemy's class registry.
    from app.models.athlete_state import AthleteState
    from app.models.macrocycle import Macrocycle
    from app.models.mesocycle import MesocycleBlock
    from app.models.objective import Objective
    from app.models.weak_point import WeakPoint
    from app.models.wellness import WellnessSample


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # === Relationships ===
    profile: Mapped["AthleteProfile"] = relationship(
        "AthleteProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Historical athlete states (one-to-many)
    athlete_states: Mapped[list["AthleteState"]] = relationship(
        "AthleteState",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Other relationships
    blocks: Mapped[list["MesocycleBlock"]] = relationship(
        "MesocycleBlock", back_populates="user"
    )
    weak_points: Mapped[list["WeakPoint"]] = relationship(
        "WeakPoint", back_populates="user"
    )
    wellness_samples: Mapped[list["WellnessSample"]] = relationship(
        "WellnessSample", back_populates="user", cascade="all, delete-orphan"
    )
    objectives: Mapped[list["Objective"]] = relationship(
        "Objective", back_populates="user", cascade="all, delete-orphan"
    )
    macrocycles: Mapped[list["Macrocycle"]] = relationship(
        "Macrocycle", back_populates="user", cascade="all, delete-orphan"
    )


class AthleteProfile(Base):
    """One-to-one profile with baseline data"""
    __tablename__ = "athlete_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_goal: Mapped[str | None] = mapped_column(Text, nullable=True)

    experience_years: Mapped[float] = mapped_column(Float, default=0.0)
    experience_level: Mapped[str] = mapped_column(String, default="beginner")
    available_days_per_week: Mapped[int] = mapped_column(Integer, default=3)
    session_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)

    equipment: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    squat_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadlift_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    bench_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    overhead_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pullup_max_reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_5k_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    run_1p5mi_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bodyweight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="profile")
