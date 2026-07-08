from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class PlanningOverride(Base):
    """
    A user-declared constraint on the plan — the athlete owning intent and
    structure (ADR-0051).

    The planner pipeline applies these after the objective blend and floors and
    before safety/confidence gates: objective blend → floors → overrides →
    safety/confidence → candidates → optimize-within → tradeoff explanation.

    ``authority`` distinguishes a ``hard_user_override`` (honored unless a safety
    gate forbids it) from a ``soft_user_preference`` (tradeable by the optimizer).
    The optimizer works *inside* the declared structure and never silently
    recomputes toward efficiency; when an override costs objective progress the
    engine surfaces the cost rather than overruling the user.
    """
    __tablename__ = "planning_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="session | week | block | range",
    )
    override_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="pin_modality_mix | pin_goal | pin_phase | min_frequency | "
        "max_frequency | include_modality | exclude_modality | movement_preference",
    )
    authority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="hard_user_override",
        comment="hard_user_override (violable only by a safety gate) | "
        "soft_user_preference (tradeable by the optimizer)",
    )

    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Type-specific parameters, e.g. {'modality': 'barbell'} or "
        "{'mix': {...}} or {'min_per_week': 2}",
    )

    # Active window (interpretation depends on scope; nulls = open-ended)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", comment="active | expired | revoked"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User")
