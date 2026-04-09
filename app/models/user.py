# app/models/user.py
from datetime import datetime
from typing import List

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import Mapped, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # === Relationships ===
    profile: Mapped["AthleteProfile"] = relationship(
        "AthleteProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # All historical athlete states (one-to-many)
    athlete_states: Mapped[List["AthleteState"]] = relationship(
        "AthleteState",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",                    # better performance for loading history
    )

    # Other relationships
    blocks: Mapped[List["MesocycleBlock"]] = relationship(
        "MesocycleBlock", back_populates="user"
    )
    weak_points: Mapped[List["WeakPoint"]] = relationship(
        "WeakPoint", back_populates="user"
    )


class AthleteProfile(Base):
    """One-to-one profile with baseline data"""
    __tablename__ = "athlete_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Experience & schedule
    experience_years = Column(Float, default=0.0)
    experience_level = Column(String, default="beginner")
    available_days_per_week = Column(Integer, default=3)
    session_duration_minutes = Column(Integer, default=60)

    # Equipment, 1RMs, body metrics, etc.
    equipment = Column(ARRAY(String), default=list)
    squat_1rm = Column(Float, nullable=True)
    deadlift_1rm = Column(Float, nullable=True)
    bench_1rm = Column(Float, nullable=True)
    overhead_1rm = Column(Float, nullable=True)
    pullup_max_reps = Column(Integer, nullable=True)
    run_5k_seconds = Column(Float, nullable=True)
    run_1p5mi_seconds = Column(Float, nullable=True)
    bodyweight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="profile")