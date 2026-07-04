"""Objective — what an athlete trains toward (Phase 4a of the goal-anchored
program plan, generalizing the running-only frontend "Goal Race" mock).

Benchmark-linked when possible, free-text otherwise:
- ``benchmark_code`` set → progress is computed from the athlete's benchmark
  observations, direction-aware via the linked ``BenchmarkDefinition``'s
  ``better_direction`` (see app.services.objective_service.compute_progress).
- ``benchmark_code`` NULL → a free-text objective (label + date + priority
  only); progress is always null (countdown only).
"""
import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist/query enums by their string *value* (e.g. ``"active"``), not
    the member *name* (``"ACTIVE"``) — mirrors app.models.mesocycle so the
    Postgres enum type (created from values, see a010 migration) round-trips
    without ``invalid input value for enum ...`` errors."""
    return [member.value for member in enum_cls]


class ObjectiveStatus(str, enum.Enum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class Objective(Base):
    """A goal an athlete is training toward — a race, a meet, a Hyrox, a
    benchmark PR, or a free-text goal not covered by a seeded benchmark.
    """
    __tablename__ = "objectives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # Nullable FK to the measurement layer. NULL means this is a free-text
    # objective — progress is always null (countdown only).
    benchmark_code: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("benchmark_definitions.code"), nullable=True
    )

    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # Prescriber-emphasis + display domain. Defaults from the linked
    # benchmark's domain when a benchmark is set and domain isn't supplied
    # (see app.services.objective_service.create_objective).
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)

    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[ObjectiveStatus] = mapped_column(
        SAEnum(ObjectiveStatus, values_callable=_enum_values),
        default=ObjectiveStatus.ACTIVE,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="objectives")
