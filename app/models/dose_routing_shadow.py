"""DoseRoutingShadowLog — shadow telemetry for Model B per-exercise dose routing (ADR-0054).

One row per ingested workout: the raw ``Σ φ·D`` routed dose (model space), its 0–100
compatibility-scaled control-space values, the versioned ``k`` scalars, and per-exercise
routing provenance. ``decision_impact`` is always ``"none_shadow_only"`` — nothing here
touches production state or a prescription. Both the **raw** (unbounded, for the future
tuning harness) and the **unclipped compat** values are stored; the raw φ·D never feeds a
live threshold. Promotion to drive state is a later PR (see the ADR).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DoseRoutingShadowLog(Base):
    __tablename__ = "dose_routing_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # The workout event time this routing is "as of".
    routed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    calibration_basis: Mapped[str] = mapped_column(String(60), nullable=False)
    # Session-level coverage tier: exercise_phi | session_modality_fallback.
    routing_basis: Mapped[str] = mapped_column(String(40), nullable=False)

    n_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_resolved_phi: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_unresolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Summary scalars (raw model space + unclipped 0–100 control space) for cheap queries.
    raw_fatigue_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw_tissue_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw_struct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fatigue_compat_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tissue_compat_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    struct_compat: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Full detail: per-axis raw + compat vectors, the k scalars, per-exercise contributions.
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    compat_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    k_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    contributions_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
