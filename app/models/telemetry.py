"""Telemetry models for adaptive engine research (prescription decisions, outcomes).

These tables provide the instrumentation needed to answer the 10 research questions.
All tables use integer PKs and JSONB blobs for flexible snapshotting.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrescriptionDecision(Base):
    """One row per prescription call. Required for Q7 (adaptive vs static) and Q8 (scoring weights)."""
    __tablename__ = "prescription_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    planned_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("planned_sessions.id"), nullable=True
    )
    goal: Mapped[str] = mapped_column(String, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="v0")
    decision_mode: Mapped[str] = mapped_column(String, nullable=False, default="adaptive")
    state_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    block_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    chosen_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    chosen_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CandidateDecisionLog(Base):
    """One row per candidate considered (not just chosen). Required for Q8 (scoring weights).

    Without rejected candidates, offline policy evaluation is weak.
    """
    __tablename__ = "candidate_decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    prescription_decision_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prescription_decisions.id"), nullable=False, index=True
    )
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_type: Mapped[str] = mapped_column(String, nullable=False)
    focus: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="generator")
    score_components_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hard_fail_reasons_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    soft_warnings_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("hard_failed", False)
        kwargs.setdefault("chosen", False)
        super().__init__(**kwargs)


class SessionFeedback(Base):
    """One row per planned session outcome. Distinguishes completed/skipped/modified/unknown.

    IMPORTANT: Do not infer followed_as_prescribed from seeded exercise logs.
    """
    __tablename__ = "session_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    planned_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("planned_sessions.id"), nullable=False, index=True, unique=True
    )
    completed_workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    followed_as_prescribed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    modified_volume: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_intensity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_exercises: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    perceived_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pain_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    soreness_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("modified_volume", False)
        kwargs.setdefault("modified_intensity", False)
        kwargs.setdefault("modified_exercises", False)
        kwargs.setdefault("pain_flag", False)
        kwargs.setdefault("soreness_flag", False)
        super().__init__(**kwargs)


class PainReport(Base):
    """Athlete-reported pain. Tissue-axis-specific. Not inferred from skips."""
    __tablename__ = "pain_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tissue_axis: Mapped[str] = mapped_column(String, nullable=False)
    severity_0_10: Mapped[float] = mapped_column(Float, nullable=False)
    affected_training: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onset: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class OutcomeEvent(Base):
    """Aggregated outcome events for risk model training.

    unknown_skip must not be classified as tissue_skip without evidence.
    """
    __tablename__ = "outcome_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    tissue_axis: Mapped[str | None] = mapped_column(String, nullable=True)
    source_table: Mapped[str | None] = mapped_column(String, nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
