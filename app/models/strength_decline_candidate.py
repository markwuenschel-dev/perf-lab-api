"""StrengthDeclineCandidate — the downward-decline state machine ledger (INT-02, ADR-0066).

A single transient low benchmark must not durably regress current strength. When a
protocol-valid ``bidirectional_update`` observation records a *material* downward
residual, canonical capacity is **not** rewritten — instead one row is opened here,
holding the evidence and the conservative prescription ceiling, and awaiting
independent corroboration before a durable, bounded regression is applied.

State machine: ``active → confirmed | dismissed | expired | safety_routed``.

Unlike the sibling ``capacity_floor_shadow_log`` (shadow-only), this ledger is
**live**: an active row conservatively constrains prescription, and a ``confirmed``
row drives a bounded estimator update of canonical capacity. Idempotency: a unique
constraint on ``(trigger_observation_id, capacity_axis, decline_policy_version)``
guarantees replaying the same observation can never open a parallel candidate, and
the trigger observation can never also confirm its own candidate.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Status vocabulary.
STATUS_ACTIVE = "active"
STATUS_CONFIRMED = "confirmed"
STATUS_DISMISSED = "dismissed"
STATUS_EXPIRED = "expired"
STATUS_SAFETY_ROUTED = "safety_routed"


class StrengthDeclineCandidate(Base):
    __tablename__ = "strength_decline_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # Canonical target identity.
    capacity_axis: Mapped[str] = mapped_column(String(30), nullable=False)
    benchmark_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("benchmark_definitions.id"), nullable=False
    )
    benchmark_code: Mapped[str] = mapped_column(String(100), nullable=False)

    # The observation that opened the candidate + its assessment occurrence.
    trigger_observation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_assessment_occurrence_id: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )

    # Evidence at candidate creation (consistent value space).
    prior_mean: Mapped[float] = mapped_column(Float, nullable=False)
    prior_variance: Mapped[float] = mapped_column(Float, nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    observation_variance: Mapped[float] = mapped_column(Float, nullable=False)
    measurement_error_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    normalized_residual: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_source: Mapped[str] = mapped_column(String(40), nullable=False)
    fatigue_readiness_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_ACTIVE, index=True
    )

    # Resolution.
    confirmation_observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("benchmark_observations.id", ondelete="SET NULL"), nullable=True
    )
    applied_posterior_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    confirmation_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    authority_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    decline_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (
        # Idempotency: one candidate per (trigger observation, axis, policy). Replay
        # of the same observation can never open a parallel candidate.
        UniqueConstraint(
            "trigger_observation_id",
            "capacity_axis",
            "decline_policy_version",
            name="uq_strength_decline_trigger_axis_policy",
        ),
    )
