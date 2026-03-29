from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Boolean
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class WorkoutLog(Base):
    """
    Persisted workout log. Created by POST /v1/log-workout.

    This is the ORM counterpart to schemas/workouts.py::WorkoutLog (the DTO).
    Storing logs separately from AthleteState rows allows replaying history
    and re-deriving S(t) if the dose engine changes.
    """
    __tablename__ = "workout_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    planned_session_id = Column(
        Integer,
        ForeignKey("planned_sessions.id"),
        nullable=True,
        index=True,
        comment="Set when this log fulfills a PlannedSession"
    )

    logged_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    session_timestamp = Column(DateTime, nullable=False, comment="When the workout actually occurred")

    modality = Column(String, nullable=False)
    duration_minutes = Column(Float, nullable=False)
    session_rpe = Column(Float, nullable=False)

    # Optional fields
    avg_rir = Column(Float, nullable=True)
    distance_meters = Column(Float, default=0.0)
    total_volume_load = Column(Float, default=0.0)

    # Human factors
    sleep_quality = Column(Float, default=5.0)
    life_stress_inverse = Column(Float, default=5.0)

    # Computed dose (stored for auditability / replay)
    dose_snapshot = Column(JSONB, nullable=True, comment="StressDose dict at time of logging")

    # For benchmark sessions: store results
    is_benchmark = Column(Boolean, default=False)
    benchmark_results = Column(
        JSONB,
        nullable=True,
        comment="e.g. {'squat_1rm': 120.0, 'run_5k_seconds': 1320}"
    )

    # Relationship
    user = relationship("User")
