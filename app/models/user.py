from datetime import datetime
from typing import List  # if you have other lists
from sqlalchemy.orm import Mapped, relationship

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy import ForeignKey  # noqa: E402 (placed after class to avoid circular)

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    athlete_state: Mapped["AthleteState"] = relationship(
        "AthleteState",
        back_populates="user",          # ← must match the name in AthleteState
        uselist=False,                  # one-to-one (most common for athlete state)
        cascade="all, delete-orphan",
    )

    # Relationships
    profile = relationship("AthleteProfile", back_populates="user", uselist=False)
    athlete_states = relationship("AthleteState", back_populates="user")
    blocks = relationship("MesocycleBlock", back_populates="user")
    weak_points = relationship("WeakPoint", back_populates="user")


class AthleteProfile(Base):
    """
    One-to-one with User. Captures baseline intake data collected at onboarding.
    Used to seed S0 and bias block generation.
    """
    __tablename__ = "athlete_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Experience
    experience_years = Column(Float, default=0.0, comment="Years of consistent training")
    experience_level = Column(
        String,
        default="beginner",
        comment="beginner | intermediate | advanced | elite"
    )

    # Schedule constraints
    available_days_per_week = Column(Integer, default=3)
    session_duration_minutes = Column(Integer, default=60, comment="Typical available session length")

    # Equipment access — stored as array of string tags
    # e.g. ["barbell", "dumbbells", "pullup_bar", "rower", "bike"]
    equipment = Column(
        ARRAY(String),
        default=list,
        comment="Equipment tags available to this athlete"
    )

    # Baseline performance — self-reported at intake, updated by benchmarks
    squat_1rm = Column(Float, nullable=True, comment="kg")
    deadlift_1rm = Column(Float, nullable=True, comment="kg")
    bench_1rm = Column(Float, nullable=True, comment="kg")
    overhead_1rm = Column(Float, nullable=True, comment="kg")
    pullup_max_reps = Column(Integer, nullable=True)
    run_5k_seconds = Column(Float, nullable=True)
    run_1p5mi_seconds = Column(Float, nullable=True)

    # Body
    bodyweight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)

    # Relationship
    user = relationship("User", back_populates="profile")
