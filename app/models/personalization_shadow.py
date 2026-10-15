"""PersonalizationShadowLog — shadow telemetry for per-athlete θ_i (never a live decision).

One row per wellness ingest for an athlete with a personalization estimate: the population
(Q2) vs personalized (partial-pooled β_i) fatigue-clearance multipliers, plus the shrinkage
``w_i`` (0 = pure population, 1 = fully personalized), the observation count ``n_i``, and the
parameter-uncertainty ``tr(P^θ)``. Capture-only: nothing here affects a prescription or state
update. See ADR-0043.

``decision_impact`` names what a row is; ``"none_shadow_only"`` marks a computed row. Readers
must recognize computed evidence positively rather than assume every row is one;
``app.services.shadow_evidence`` is the reader that does.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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


class PersonalizationShadowLog(Base):
    __tablename__ = "personalization_shadow_log"
    # P7 correction chains (a062): see the migration's docstring. Unlinked rows (all three
    # provenance columns NULL) sit outside every key.
    __table_args__ = (
        CheckConstraint(
            "(source_wellness_sample_id IS NULL AND content_hash IS NULL AND supersedes_id IS NULL)"
            " OR (source_wellness_sample_id IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_personalization_shadow_provenance_shape",
        ),
        # Every link points strictly backwards, so the chain can hold no cycle.
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id < id", name="ck_personalization_shadow_supersedes_older"
        ),
        # The sample is this row's athlete's own.
        ForeignKeyConstraint(
            ["source_wellness_sample_id", "user_id"],
            ["wellness_samples.id", "wellness_samples.user_id"],
            ondelete="CASCADE",
            name="fk_personalization_shadow_source_wellness_sample",
        ),
        UniqueConstraint("id", "source_wellness_sample_id", "model_version", "parameter", name="uq_personalization_shadow_chain_member"),
        ForeignKeyConstraint(
            ["supersedes_id", "source_wellness_sample_id", "model_version", "parameter"],
            ["personalization_shadow_log.id", "personalization_shadow_log.source_wellness_sample_id", "personalization_shadow_log.model_version", "personalization_shadow_log.parameter"],
            ondelete="CASCADE",
            name="fk_personalization_shadow_supersedes_same_chain",
        ),
        Index(
            "uq_personalization_shadow_chain_root",
            "source_wellness_sample_id", "model_version", "parameter",
            unique=True,
            postgresql_where=text("source_wellness_sample_id IS NOT NULL AND supersedes_id IS NULL"),
        ),
        Index(
            "uq_personalization_shadow_supersedes_once",
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

    # The wellness sample this row was computed from, the hash of the hook's inputs from it, and
    # the row of the same chain it corrects. All NULL on rows written before P7b.
    source_wellness_sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
