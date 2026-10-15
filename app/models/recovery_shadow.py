"""RecoveryShadowLog — shadow telemetry for Q2 recovery priors (never a live decision).

At each wellness ingest this records the baseline (production) vs learned (shadow-override)
fatigue-clearance multipliers for the athlete's current fatigue state. The next-day
recovery-proxy outcome is not stored inline; joining it offline is the intended use.
Capture-only: no value here affects a prescription or state update.

``decision_impact`` names what a row is. ``"none_shadow_only"`` marks a computed row. From P7b,
``"skipped_not_as_of_day"`` marks a row recording that the shadow declined to compute (the
sample was not of the as-of UTC day), with empty multipliers. Readers must recognize computed
evidence positively; ``app.services.shadow_evidence`` is the reader that does.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RecoveryShadowLog(Base):
    __tablename__ = "recovery_shadow_log"
    # P7 correction chains (a062): see the migration's docstring. Unlinked rows (all three
    # provenance columns NULL) sit outside every key.
    __table_args__ = (
        CheckConstraint(
            "(source_wellness_sample_id IS NULL AND content_hash IS NULL AND supersedes_id IS NULL)"
            " OR (source_wellness_sample_id IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_recovery_shadow_provenance_shape",
        ),
        # Every link points strictly backwards, so the chain can hold no cycle.
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id < id", name="ck_recovery_shadow_supersedes_older"
        ),
        # The sample is this row's athlete's own.
        ForeignKeyConstraint(
            ["source_wellness_sample_id", "user_id"],
            ["wellness_samples.id", "wellness_samples.user_id"],
            ondelete="CASCADE",
            name="fk_recovery_shadow_source_wellness_sample",
        ),
        UniqueConstraint("id", "source_wellness_sample_id", "model_version", name="uq_recovery_shadow_chain_member"),
        ForeignKeyConstraint(
            ["supersedes_id", "source_wellness_sample_id", "model_version"],
            ["recovery_shadow_log.id", "recovery_shadow_log.source_wellness_sample_id", "recovery_shadow_log.model_version"],
            ondelete="CASCADE",
            name="fk_recovery_shadow_supersedes_same_chain",
        ),
        Index(
            "uq_recovery_shadow_chain_root",
            "source_wellness_sample_id", "model_version",
            unique=True,
            postgresql_where=text("source_wellness_sample_id IS NOT NULL AND supersedes_id IS NULL"),
        ),
        Index(
            "uq_recovery_shadow_supersedes_once",
            "supersedes_id",
            unique=True,
            postgresql_where=text("supersedes_id IS NOT NULL"),
        ),
    )

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

    # The wellness sample this row was computed from, the hash of the hook's inputs from it, and
    # the row of the same chain it corrects. All NULL on rows written before P7b.
    source_wellness_sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
