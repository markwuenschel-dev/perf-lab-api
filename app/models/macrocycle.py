"""Macrocycle — a thin program container above training blocks (Phase 5 of the
goal-anchored program plan).

A macrocycle anchors a run of training to a single Objective and yields a real
cross-block "week X of Y". Per ADR-0040 it is deliberately thin:

- It owns only its anchor (``objective_id``), its ``start_date``, and a status.
- ``target_date`` is NOT stored — it is read from the anchor Objective at compute
  time, so the Objective stays the single source of truth (PDR-0004).
- The block sequence is NEVER persisted ahead of time. Blocks are generated and
  adapted one at a time by the engine and merely reference their macrocycle via a
  nullable ``mesocycle_blocks.macrocycle_id`` FK — the container is a horizon the
  engine fills as it goes, not a script it must follow (PDR-0008, "plan is a seed,
  not a rail").
"""
import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.mesocycle import MesocycleBlock
    from app.models.objective import Objective
    from app.models.user import User


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist/query enums by their string *value* (e.g. ``"active"``), not the
    member *name* (``"ACTIVE"``) — mirrors app.models.objective / app.models.mesocycle
    so the Postgres enum type (created from values, see a011 migration) round-trips
    without ``invalid input value for enum ...`` errors."""
    return [member.value for member in enum_cls]


class MacrocycleStatus(str, enum.Enum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class Macrocycle(Base):
    """A program container anchoring a run of training blocks to one Objective."""
    __tablename__ = "macrocycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # The anchor. A macrocycle exists to serve one Objective; deleting that
    # objective removes the macrocycle (DB-level ON DELETE CASCADE, mirrored by
    # the ORM cascade on Objective.macrocycles) so no orphaned horizon is left.
    objective_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("objectives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The macrocycle owns its own start; "week X of Y" is computed from this plus
    # the anchor objective's target_date (never stored here).
    start_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[MacrocycleStatus] = mapped_column(
        SAEnum(MacrocycleStatus, values_callable=_enum_values),
        default=MacrocycleStatus.ACTIVE,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="macrocycles")
    objective: Mapped["Objective"] = relationship(
        "Objective", back_populates="macrocycles"
    )
    # Blocks reference the macrocycle via a nullable FK; deleting a macrocycle
    # detaches its blocks (ON DELETE SET NULL) rather than deleting them.
    blocks: Mapped[list["MesocycleBlock"]] = relationship(
        "MesocycleBlock", back_populates="macrocycle"
    )
