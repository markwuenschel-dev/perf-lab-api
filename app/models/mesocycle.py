import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.macrocycle import Macrocycle
    from app.models.user import User


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist/query enums by their string *value* (e.g. ``"active"``), not the
    member *name* (``"ACTIVE"``). The Postgres enum types were created from the
    values (see the a000/a001 migrations), so without this SQLAlchemy's default
    name-based binding raises ``invalid input value for enum ...`` on every
    read/write of these columns."""
    return [member.value for member in enum_cls]


class BlockGoal(str, enum.Enum):
    STRENGTH = "Strength"
    HYPERTROPHY = "Hypertrophy"
    POWER = "Power"
    HYROX = "Hyrox"
    CROSSFIT = "CrossFit"
    RUNNING = "Running"
    CALISTHENICS = "Calisthenics"
    GENERAL = "General"
    RECOMP = "Recomp"


class BlockStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SessionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    RESCHEDULED = "rescheduled"


class MesocycleBlock(Base):
    """
    A training block — the macro container for a period of goal-directed training.

    weekly_template is a JSONB array of session slot descriptors, one per
    planned training day. Example for a 3-day Strength block:
    [
        {"day_of_week": 1, "category": "Heavy Lower", "modality": "Strength"},
        {"day_of_week": 3, "category": "Heavy Upper", "modality": "Strength"},
        {"day_of_week": 5, "category": "Accessory + Conditioning", "modality": "Mixed"}
    ]

    modality_mix is a JSONB dict describing the intended emphasis split, e.g.:
    {"strength": 0.5, "hypertrophy": 0.3, "conditioning": 0.2}

    The prescriber reads the relevant slot from weekly_template for today's
    PlannedSession category, then uses S(t) to constrain intensity and volume,
    and weak points to bias exercise selection.
    """
    __tablename__ = "mesocycle_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # Optional parent macrocycle (Phase 5). Nullable — a block can exist without
    # one; deleting the macrocycle sets this back to NULL (ON DELETE SET NULL)
    # rather than deleting the block.
    macrocycle_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("macrocycles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    goal: Mapped[BlockGoal] = mapped_column(
        SAEnum(BlockGoal, values_callable=_enum_values), nullable=False
    )
    status: Mapped[BlockStatus] = mapped_column(
        SAEnum(BlockStatus, values_callable=_enum_values),
        default=BlockStatus.ACTIVE,
        nullable=False,
    )

    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    sessions_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # computed: start_date + duration_weeks * 7

    # Modality emphasis split (sum should ~ 1.0)
    modality_mix: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    # Ordered list of session slot dicts (see docstring)
    weekly_template: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )

    # Block-level notes / LLM rationale for the generated plan
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)

    # Per-block session preferences (Phase 3a — goal-anchored program).
    # Desired session length in minutes for sessions in this block; the
    # prescriber nudges rx.duration_min toward this, overriding the plain
    # periodization scaling for the final value.
    target_session_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # One of "minimal" | "balanced" | "high"; missing/None is treated as
    # "balanced" by the prescriber. Controls how many accessory exercise
    # slots are appended after the winning template's primary exercise_slots.
    accessory_emphasis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # List of movement-pattern tag strings (e.g. ["posterior_chain", "push"])
    # the athlete wants extra accessory work on. Missing/None means no
    # specific focus — the prescriber falls back to active weak-point tags.
    accessory_focus: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Deload configuration
    deload_every_n_weeks: Mapped[int] = mapped_column(Integer, default=4)
    deload_volume_factor: Mapped[float] = mapped_column(
        Float, default=0.6, comment="Volume multiplier during deload week"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="blocks")
    macrocycle: Mapped["Macrocycle | None"] = relationship(
        "Macrocycle", back_populates="blocks"
    )
    planned_sessions: Mapped[list["PlannedSession"]] = relationship(
        "PlannedSession",
        back_populates="block",
        cascade="all, delete-orphan"
    )


class PlannedSession(Base):
    """
    A single scheduled training slot within a MesocycleBlock.

    Bridges the macro plan to the daily view. One row per day the athlete
    is expected to train. Created in bulk when a block is started, then
    updated as sessions are completed or skipped.

    prescribed_content is populated lazily by the prescriber when the user
    opens "today's session". It contains the LLM-generated session detail:
    {
        "type": "Max Strength",
        "focus": "Back Squat 5x3 @ RPE 8",
        "exercises": [...],
        "rationale": "...",
        "duration_min": 60
    }
    """
    __tablename__ = "planned_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    block_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mesocycle_blocks.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Set the first time a session is moved, so the original plan date is not lost.
    original_scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    week_number: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1-indexed week within the block"
    )
    day_of_week: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1=Mon, 7=Sun"
    )

    # Slot descriptor from weekly_template (denormalized for query convenience)
    category: Mapped[str] = mapped_column(
        String, nullable=False, comment="e.g. 'Heavy Lower', 'Conditioning'"
    )
    modality: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, values_callable=_enum_values),
        default=SessionStatus.PENDING,
        nullable=False,
    )

    # Populated when prescriber is called for this session
    prescribed_content: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    # Populated when user logs the session
    workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id"), nullable=True
    )

    # Whether this is a deload session (affects prescriber intensity targets)
    is_deload: Mapped[bool] = mapped_column(Boolean, default=False)
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark_key: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="e.g. periodic_retest, block_exit"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    block: Mapped["MesocycleBlock"] = relationship(
        "MesocycleBlock", back_populates="planned_sessions"
    )
