"""MpcShadowLog — shadow telemetry for the MPC planner (never a live decision).

One row per prescription: what the receding-horizon MPC planner *would* have chosen
(``mpc_*``) versus what the greedy prescriber *did* choose (``greedy_*``), whether they
agree, the belief uncertainty used, and the per-candidate objective breakdown. Capture-only:
``decision_impact`` is always ``"none_shadow_only"`` — nothing here affects a prescription
or state update. See ADR-0042.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MpcShadowLog(Base):
    __tablename__ = "mpc_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    goal: Mapped[str] = mapped_column(String(80), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)

    greedy_branch_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    greedy_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mpc_branch_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mpc_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agreement: Mapped[bool] = mapped_column(Boolean, nullable=False)

    belief_trace: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_scores_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    weights_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
