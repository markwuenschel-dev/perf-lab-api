"""RecoveryShadowLog — shadow telemetry for Q2 recovery priors (never a live decision).

At each wellness ingest this records the baseline (production) vs learned (shadow-override)
fatigue-clearance multipliers for the athlete's current fatigue state. The next-day
recovery-proxy OUTCOME is joined offline (feature-builder style), not stored inline.
``decision_impact`` is always ``"none_shadow_only"`` — this table is capture-only and no
value here affects a prescription or state update.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RecoveryShadowLog(Base):
    __tablename__ = "recovery_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # The learned artifact version (or "none" when no override is present).
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)

    wellness: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fatigue_before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_clearance_multiplier: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    learned_clearance_multiplier: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
