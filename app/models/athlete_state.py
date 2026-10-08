# app/models/athlete_state.py
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, text
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
    __table_args__ = (
        # One correction head per receipt. See alembic a056.
        Index(
            "uq_athlete_states_source_correction",
            "source_correction_id",
            unique=True,
            postgresql_where=text("source_correction_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), index=True
    )
    # P3a: the event that wrote this row (a workout, or a benchmark observation). NULL for
    # baselines and for rows written before P3a.
    source_workout_log_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "workout_logs.id", ondelete="SET NULL", name="fk_athlete_states_source_workout_log_id"
        ),
        nullable=True,
    )
    source_observation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "benchmark_observations.id",
            ondelete="SET NULL",
            name="fk_athlete_states_source_observation_id",
        ),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    # P3b-1: what kind of event wrote this row, which row it was computed from, and which
    # operator/parameter identity computed it. NULL on rows written before P3b-1: such a row
    # cannot be replayed. See alembic a055.
    event_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    predecessor_state_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "athlete_states.id", ondelete="SET NULL", name="fk_athlete_states_predecessor_state_id"
        ),
        nullable=True,
    )
    transition_identity: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "engine_transition_identities.digest", name="fk_athlete_states_transition_identity"
        ),
        nullable=True,
    )
    # P3b-2: set exactly on a correction head (event_kind = 'correction'): the receipt for the
    # exact-replay correction that produced it. See alembic a056.
    source_correction_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("state_corrections.id", name="fk_athlete_states_source_correction_id"),
        nullable=True,
    )
    # A baseline's own timestamp is the wall-clock time it was created. The first training
    # event re-anchors it to just before that event; this keeps the timestamp it had.
    anchored_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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
