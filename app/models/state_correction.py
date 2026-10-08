from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StateCorrection(Base):
    """Receipt for one exact-replay correction (P3b-2). Append-only; never a training event.

    The correction head is the ``athlete_states`` row whose ``source_correction_id`` is this
    receipt's id. See alembic a056.
    """

    __tablename__ = "state_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String, nullable=False)
    transition_identity: Mapped[str] = mapped_column(
        String(64), ForeignKey("engine_transition_identities.digest"), nullable=False
    )
    checkpoint_state_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("athlete_states.id"), nullable=False
    )
    head_before_state_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("athlete_states.id"), nullable=False
    )
    # Timestamp of the earliest event this correction introduced: rows after it, stored before
    # the correction head, are out of date.
    affected_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StateCorrectionEvent(Base):
    """An event a correction introduced into the state history, in batch order."""

    __tablename__ = "state_correction_events"
    __table_args__ = (
        UniqueConstraint("correction_id", "ordinal", name="uq_state_correction_events_ordinal"),
        CheckConstraint(
            "(workout_log_id IS NULL) <> (observation_id IS NULL)",
            name="ck_state_correction_events_one_event",
        ),
        # An event is introduced at most once, ever.
        Index(
            "uq_state_correction_events_workout",
            "workout_log_id",
            unique=True,
            postgresql_where=text("workout_log_id IS NOT NULL"),
        ),
        Index(
            "uq_state_correction_events_observation",
            "observation_id",
            unique=True,
            postgresql_where=text("observation_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    correction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("state_corrections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id"), nullable=True
    )
    observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("benchmark_observations.id"), nullable=True
    )
