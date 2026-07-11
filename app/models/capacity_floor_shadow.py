"""CapacityFloorShadowLog — shadow candidates for the deferred upward_lower_bound
capacity floor-ratchet (ADR-0058).

When a qualifying observation resolves an ``upward_lower_bound`` capacity_effect, the
authority is real but the live floor-ratchet is **not promoted** — the deployed
ADR-0055 invariant (a workout-derived estimate never mutates canonical capacity)
stays active. This table records *resolved authority and applied transition
separately*: one row per such observation capturing the **proposed floor**, the
**projected uplift**, the **application-policy version**, and the **reason mutation
was not applied**. ``decision_impact`` is always ``"none_shadow_only"`` — nothing
here touches production state.

Live activation requires a separate, observable promotion decision supported by this
shadow evidence, plus idempotency proof, bounded-uplift guards, canary rollout, and
rollback capability.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CapacityFloorShadowLog(Base):
    __tablename__ = "capacity_floor_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    benchmark_observation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    benchmark_code: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # --- Resolved authority (recorded separately from the applied transition) ----
    capacity_effect: Mapped[str] = mapped_column(String(30), nullable=False)
    authority_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    authority_resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Application policy (why the floor was NOT applied to live state) ---------
    application_policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    not_applied_reason: Mapped[str] = mapped_column(String(60), nullable=False)

    # --- The candidate the promotion decision will evaluate ----------------------
    # Proposed floor = the per-axis capacity the ratchet would clamp up to.
    proposed_floor_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # Projected uplift = per-axis positive delta (proposed_floor − prior); {} when the
    # lower bound is below the current watermark (it would raise nothing).
    projected_uplift_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    projected_uplift_total: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    would_raise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
