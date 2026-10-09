from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workout_set_log import WorkoutSetLog


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
    # Exactly which prescription revision this log was linked to (P1, a051).
    prescription_revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "prescription_revisions.id",
            ondelete="SET NULL",
            name="fk_workout_logs_prescription_revision_id",
        ),
        nullable=True,
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    session_timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="When the workout actually occurred"
    )
    # P3a: how session_timestamp was chosen, and the raw input it came from (audit).
    timestamp_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    client_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # P3a: whether this workout updated the training state. NULL = logged before P3a.
    state_disposition: Mapped[str | None] = mapped_column(String, nullable=True)
    state_disposition_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    modality: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    session_rpe: Mapped[float] = mapped_column(Float, nullable=False)

    # Optional fields
    avg_rir: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float] = mapped_column(Float, default=0.0)
    total_volume_load: Mapped[float] = mapped_column(Float, default=0.0)

    # Human factors (ADR-0049: missing wellness is a gap, not an imputation).
    # SQL NULL means "no check-in exists for this session" and must stay distinguishable
    # from a real mid-scale report; the previous python-side ``default=5.0`` made every
    # un-checked-in session indistinguishable from an athlete who reported 5/10, and the
    # non-Optional annotation contradicted the column, which a000 already declared
    # nullable. Both columns are already ``nullable=True`` in
    # ``alembic/versions/a000_init_foundational_tables.py`` (lines 184-185), so this is a
    # model-side correction only — no migration is required.
    sleep_quality: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    life_stress_inverse: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )

    # Computed dose (stored for auditability / replay)
    dose_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="StressDose dict at time of logging"
    )

    # P3b-1: the operator's inputs exactly as the state transition saw them (final modality,
    # movement pattern, wellness, and the dose actually used). Written once, immutable (a DB
    # trigger refuses a change). NULL = logged before P3b-1: not replayable. See alembic a055.
    replay_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # P3b-3: the stable code that kept this record-only event out of the state when a fold was
    # attempted (late_event_service); NULL = not attempted, or folded in. Mutable (a retry
    # overwrites it). See alembic a057.
    replay_refusal: Mapped[str | None] = mapped_column(String, nullable=True)

    # For benchmark sessions: store results
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark_results: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="e.g. {'squat_1rm': 120.0, 'run_5k_seconds': 1320}"
    )

    # Relationships
    user: Mapped["User"] = relationship("User")
    set_logs: Mapped[list["WorkoutSetLog"]] = relationship(
        "WorkoutSetLog",
        back_populates="workout_log",
        cascade="all, delete-orphan",
        order_by="WorkoutSetLog.set_index",
    )
