from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.exercise import Exercise
    from app.models.workout_log import WorkoutLog


class WorkoutSetLog(Base):
    """
    A single logged set — the atomic unit of a workout (ADR-0045).

    A ``WorkoutLog`` is the session header; its sets live here as queryable
    child rows (never a JSONB blob). A set binds to a catalog ``Exercise`` and
    the exercise's ``load_type`` types which fields are meaningful
    (``barbell/dumbbell`` → ``load_kg`` + ``reps`` + ``rpe``; ``bodyweight`` →
    ``reps`` (+ ``band``/``elevation``); ``time`` → ``duration_s``;
    ``distance`` → ``distance_m`` (+ ``duration_s`` for pace)). Movements not
    yet in the catalog log via ``free_text_name`` with ``exercise_id`` null.

    Sets are the system of record; measurement (e1RM/PR/progression) flows
    through ``benchmark_observations`` via write-time extraction, never by
    scanning these rows (PDR-0003).
    """
    __tablename__ = "workout_set_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workout_log_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workout_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    set_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Order of this set within the session (0-based)"
    )

    exercise_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("exercises.id"),
        nullable=True,
        index=True,
        comment="Catalog exercise; null when logged via free_text_name",
    )
    free_text_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="Fallback for movements not yet in the catalog (no benchmark linkage)",
    )
    load_type: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="Snapshot of the exercise load_type at log time (types the fields)",
    )

    # load_type-typed fields (all optional; which are meaningful depends on load_type)
    load_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    rir: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_top_set: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Heaviest/hardest set of its exercise this session; drives e1RM extraction",
    )

    # Bodyweight modifiers
    band: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="Assistance/resistance band, e.g. 'green'"
    )
    elevation: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="Deficit/box height / elevation modifier"
    )
    tempo: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    workout_log: Mapped["WorkoutLog"] = relationship(
        "WorkoutLog", back_populates="set_logs"
    )
    exercise: Mapped["Exercise | None"] = relationship("Exercise")
