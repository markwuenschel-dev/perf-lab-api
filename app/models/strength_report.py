import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UnderivedStrengthReport(Base):
    """A reported set the server records but derives nothing from (P4-3a).

    Its effort is unknown or conflicting, so it carries no e1RM, prescription permission,
    capacity effect or decline input. The facts are permanent (a DB trigger refuses an edit or a
    deletion); only the supersession columns may be set, once. See alembic a058.
    """

    __tablename__ = "underived_strength_reports"
    __table_args__ = (
        CheckConstraint("method = 'rep_set'", name="ck_underived_strength_reports_method"),
        CheckConstraint(
            "collection_mode IN ('onboarding_onramp', 'retest')",
            name="ck_underived_strength_reports_collection_mode",
        ),
        # The request schema's bounds. NaN and infinity compare above every finite float in
        # PostgreSQL, so the upper bounds also refuse them.
        CheckConstraint(
            "load_kg > 0 AND load_kg < 'Infinity' AND reps >= 1",
            name="ck_underived_strength_reports_set",
        ),
        CheckConstraint(
            "rpe IS NULL OR (rpe >= 1 AND rpe <= 10)", name="ck_underived_strength_reports_rpe"
        ),
        CheckConstraint(
            "rir IS NULL OR (rir >= 0 AND rir <= 10)", name="ck_underived_strength_reports_rir"
        ),
        CheckConstraint(
            "(reason = 'effort_unknown' AND rpe IS NULL AND rir IS NULL) "
            "OR (reason = 'effort_conflict' AND rpe IS NOT NULL AND rir IS NOT NULL)",
            name="ck_underived_strength_reports_reason",
        ),
        CheckConstraint(
            "(superseded_by_observation_id IS NULL) = (superseded_at IS NULL)",
            name="ck_underived_strength_reports_superseded",
        ),
        Index(
            "ix_underived_strength_reports_lookup",
            "user_id",
            "benchmark_definition_id",
            "performed_at",
        ),
        # A report is superseded at most once, and an observation supersedes at most one report.
        Index(
            "uq_underived_strength_reports_superseded_by",
            "superseded_by_observation_id",
            unique=True,
            postgresql_where=text("superseded_by_observation_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    benchmark_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("benchmark_definitions.id"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    collection_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    # When the set was performed (naive UTC). NULL means unknown and stays unknown.
    performed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    load_kg: Mapped[float] = mapped_column(Float, nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    rir: Mapped[float | None] = mapped_column(Float, nullable=True)
    # effort_unknown | effort_conflict
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    superseded_by_observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("benchmark_observations.id"), nullable=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StrengthSubmissionKey(Base):
    """One keyed strength submission and the outcome it first returned (P4-3a).

    Written in the same transaction as its effect; append-only. A replay of the key with the same
    payload answers from ``outcome`` and repeats no effect. The key is unique per athlete, but the
    schema does not check that the effect it names belongs to that athlete: the writer must
    refuse a foreign-owned observation or report before storing an outcome. See alembic a059.
    """

    __tablename__ = "strength_submission_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_strength_submission_keys_key"),
        CheckConstraint(
            "(observation_id IS NULL) <> (report_id IS NULL)",
            name="ck_strength_submission_keys_one_effect",
        ),
        # An effect belongs to at most one key.
        Index(
            "uq_strength_submission_keys_observation",
            "observation_id",
            unique=True,
            postgresql_where=text("observation_id IS NOT NULL"),
        ),
        Index(
            "uq_strength_submission_keys_report",
            "report_id",
            unique=True,
            postgresql_where=text("report_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("benchmark_observations.id"), nullable=True
    )
    report_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("underived_strength_reports.id"), nullable=True
    )
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
