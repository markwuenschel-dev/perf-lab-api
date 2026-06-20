from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class WorkoutLog(Base):
    """
    Persisted workout log. Created by POST /v1/log-workout.

    This is the ORM counterpart to schemas/workouts.py::WorkoutLog (the DTO).
    Storing logs separately from AthleteState rows allows replaying history
    and re-deriving S(t) if the dose engine changes.
    """
    __tablename__ = "workout_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    planned_session_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("planned_sessions.id"),
        nullable=True,
        index=True,
        comment="Set when this log fulfills a PlannedSession"
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    session_timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="When the workout actually occurred"
    )

    modality: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    session_rpe: Mapped[float] = mapped_column(Float, nullable=False)

    # Optional fields
    avg_rir: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float] = mapped_column(Float, default=0.0)
    total_volume_load: Mapped[float] = mapped_column(Float, default=0.0)

    # Human factors
    sleep_quality: Mapped[float] = mapped_column(Float, default=5.0)
    life_stress_inverse: Mapped[float] = mapped_column(Float, default=5.0)

    # Computed dose (stored for auditability / replay)
    dose_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="StressDose dict at time of logging"
    )

    # For benchmark sessions: store results
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark_results: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="e.g. {'squat_1rm': 120.0, 'run_5k_seconds': 1320}"
    )

    # Relationship
    user: Mapped["User"] = relationship("User")
