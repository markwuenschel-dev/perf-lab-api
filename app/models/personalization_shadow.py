"""PersonalizationShadowLog — shadow telemetry for per-athlete θ_i (never a live decision).

One row per wellness ingest for an athlete with a personalization estimate: the population
(Q2) vs personalized (partial-pooled β_i) fatigue-clearance multipliers, plus the shrinkage
``w_i`` (0 = pure population, 1 = fully personalized), the observation count ``n_i``, and the
parameter-uncertainty ``tr(P^θ)``. Capture-only: ``decision_impact`` is always
``"none_shadow_only"`` — nothing here affects a prescription or state update. See ADR-0043.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PersonalizationShadowLog(Base):
    __tablename__ = "personalization_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    parameter: Mapped[str] = mapped_column(String(40), nullable=False, default="recovery_beta")
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)

    n_obs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shrinkage_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # w_i
    theta_trace: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # tr(P^θ_i)

    wellness: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    population_multiplier: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    personalized_multiplier: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
