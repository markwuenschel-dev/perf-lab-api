"""EkfShadowLog — shadow telemetry for the full-covariance EKF (never a live decision).

One row per EKF step in the parallel shadow estimator (ADR-0041). ``event_type`` names the
transition operator; the observation *source* is carried orthogonally by
``source_wellness_sample_id`` (AUD-C8):

- ``event_type="predict"`` — written when a workout is ingested; records the belief after
  propagating the covariance through the deterministic twin.
- ``event_type="update"`` — a measurement correction: a benchmark (source NULL) or an original
  wellness assimilation (source non-NULL), plus innovation/gain/trace diagnostics.
- ``event_type="replay"`` (source non-NULL) — a corrected wellness observation replayed from its
  predecessor belief when it was still the effective head; supersedes the row it corrects and
  links back to the belief it rebuilt from (head-correction replay, a038).

``mean_json``/``variance_json`` are per-axis maps keyed ``"domain.key"`` in normalized
space; ``covariance_json`` is the full 22x22 matrix as a nested list (enough to rehydrate
the belief for the next step and to compute offline calibration). ``decision_impact`` is
always ``"none_shadow_only"`` — nothing here affects a prescription or production state.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EkfShadowLog(Base):
    __tablename__ = "ekf_shadow_log"
    __table_args__ = (
        # AUD-C8/replay: at most one ORIGINAL wellness assimilation per (observation, model).
        # Scoped POSITIVELY to event_type='update' (Q9) so replay rows (event_type='replay') for
        # the same (sample, model) coexist, and a future source-carrying event type cannot
        # silently inherit this uniqueness. The concurrency authority for at-most-once assimilation.
        Index(
            "uq_ekf_original_wellness_source_model",
            "source_wellness_sample_id",
            "model_version",
            unique=True,
            postgresql_where=text(
                "source_wellness_sample_id IS NOT NULL AND event_type = 'update'"
            ),
        ),
        # Replay idempotency: one replay per source observation, per model, per correction
        # generation — keyed by source identity, not lineage, so retries under a shifting head
        # cannot admit a second replay for the same generation.
        Index(
            "uq_ekf_wellness_replay_revision",
            "source_wellness_sample_id",
            "model_version",
            "correction_revision",
            unique=True,
            postgresql_where=text(
                "source_wellness_sample_id IS NOT NULL AND event_type = 'replay'"
            ),
        ),
        # A row linked to a wellness observation must carry complete hash provenance;
        # legacy/unlinked rows stay NULL. Prevents a half-populated new identity.
        CheckConstraint(
            "source_wellness_sample_id IS NULL "
            "OR (assimilated_content_hash IS NOT NULL AND latest_seen_content_hash IS NOT NULL)",
            name="ck_ekf_shadow_wellness_hash_complete",
        ),
        # A replay row must carry complete lineage provenance.
        CheckConstraint(
            "event_type <> 'replay' OR ("
            "source_wellness_sample_id IS NOT NULL AND supersedes_log_id IS NOT NULL "
            "AND replay_base_log_id IS NOT NULL AND correction_revision > 0)",
            name="ck_ekf_replay_lineage_complete",
        ),
        # Reconciliation revisions are never negative and never ahead of the correction generation.
        CheckConstraint(
            "replayed_revision >= 0 AND replayed_revision <= correction_revision",
            name="ck_ekf_replayed_revision_bounds",
        ),
        # On ORIGINAL source-backed update rows, the sticky flag exactly materializes whether a
        # correction generation remains unreplayed. Replay rows carry a correction revision but are
        # not themselves reconciliation records, so they are intentionally outside this equivalence.
        CheckConstraint(
            "event_type <> 'update' OR source_wellness_sample_id IS NULL OR "
            "correction_requires_replay = (replayed_revision < correction_revision)",
            name="ck_ekf_original_replay_flag_consistent",
        ),
    )

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
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # predict | update | replay

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

    # ── AUD-C8: wellness-observation idempotency ────────────────────────────────────────
    # All nullable — legacy rows and predict/benchmark rows carry no wellness source. New
    # wellness-writer code must always populate the source id + both hashes (enforced by the
    # CHECK above and a service-level assertion). CASCADE matches the sibling shadow tables
    # (capacity_floor/dose_routing/strength_decline -> their source), so a privacy deletion
    # of the wellness sample removes its derived shadow rows.
    source_wellness_sample_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("wellness_samples.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Hash represented by the effective lineage. It advances only after a replay is durably
    # appended and revision-guarded reconciliation succeeds; historical numerical rows stay fixed.
    assimilated_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Hash of the most recently received content for this observation (advances on corrections).
    latest_seen_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sticky: set when a correction (changed content, same identity) arrives; cleared only by
    # a real replay that rebuilds the trajectory, never by a later identical retry. Materializes
    # the invariant ``replayed_revision < correction_revision`` (kept consistent by the classifier
    # and reconciliation).
    correction_requires_replay: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    correction_detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── AUD-C8: head-correction replay lineage (a038) ───────────────────────────────────
    # Monotonic correction generation on the ORIGINAL assimilation row: bumped once per changed
    # ``latest_seen_content_hash`` (so A→B→A counts as two generations, not zero). Replay rows
    # carry the generation they reproduce (> 0).
    correction_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Highest generation whose replay has been durably appended (on the original row). The flag is
    # true exactly while this trails ``correction_revision``.
    replayed_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Provenance (nullable; populated on the relevant row). ``replayed_by_log_id`` lives on the
    # original row → the replay that resolved it; ``supersedes``/``replay_base`` live on the
    # replay row → the head it replaced and the predecessor belief it rebuilt from.
    replayed_by_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ekf_shadow_log.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ekf_shadow_log.id", ondelete="SET NULL"), nullable=True
    )
    replay_base_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ekf_shadow_log.id", ondelete="SET NULL"), nullable=True
    )
