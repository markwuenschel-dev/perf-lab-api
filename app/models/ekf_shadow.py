"""EkfShadowLog — shadow telemetry for the full-covariance EKF (never a live decision).

One row per EKF step in the parallel shadow estimator (ADR-0041):

- ``event_type="predict"`` — written when a workout is ingested; records the belief after
  propagating the covariance through the deterministic twin.
- ``event_type="update"`` — written when a benchmark is assimilated; records the belief
  after the joint measurement correction, plus innovation/gain/trace diagnostics.

``mean_json``/``variance_json`` are per-axis maps keyed ``"domain.key"`` in normalized
space; ``covariance_json`` is the full 22x22 matrix as a nested list (enough to rehydrate
the belief for the next step and to compute offline calibration). ``decision_impact`` is
always ``"none_shadow_only"`` — nothing here affects a prescription or production state.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EkfShadowLog(Base):
    __tablename__ = "ekf_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # Timestamp the belief is valid "as of" (workout/observation time).
    belief_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # predict | update

    mean_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    variance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    covariance_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    # Update-only diagnostics (null on predict rows).
    benchmark_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    innovation: Mapped[float | None] = mapped_column(Float, nullable=True)
    gain_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace_pre: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace_post: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Normalized innovation squared νᵀS⁻¹ν and its dof (n observed axes). A calibrated
    # filter has E[nis] = n_obs; the ratio is the core offline consistency check.
    nis: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_obs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
