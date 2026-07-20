"""AUD-C8: EKF head-correction replay — lineage columns + revision-scoped uniqueness.

Consumes the ``correction_requires_replay`` flag a037 introduced. A corrected wellness
observation that is still the effective EKF head is repaired by replaying the trusted
update kernel from the exact original predecessor belief with the corrected content and
appending an ``event_type='replay'`` row; this migration adds the lineage/revision bookkeeping
and re-scopes uniqueness so an original assimilation and its replays coexist.

``event_type`` stays the transition-operator vocabulary (predict|update|replay); the wellness
*source* dimension remains ``source_wellness_sample_id`` (Q9). So original-wellness uniqueness is
scoped **positively** to ``(source non-null AND event_type='update')`` — a future source-carrying
event type cannot silently inherit the contract — and replay idempotency to
``(source non-null AND event_type='replay')`` keyed by ``correction_revision``. No event_type
backfill: existing wellness rows keep ``event_type='update'``.

Column shapes are inlined; migrations stay replayable independent of app refactors.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a038_ekf_head_correction_replay"
down_revision: str | None = "a037_ekf_wellness_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LINEAGE_FKS = ("replayed_by_log_id", "supersedes_log_id", "replay_base_log_id")


def upgrade() -> None:
    # Revision + lineage bookkeeping. New columns begin at zero; sticky a037 corrections are
    # backfilled to generation 1 below before the flag/revision invariant is installed.
    op.add_column(
        "ekf_shadow_log",
        sa.Column("correction_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ekf_shadow_log",
        sa.Column("replayed_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("ekf_shadow_log", sa.Column("replayed_at", sa.DateTime(), nullable=True))
    op.add_column("ekf_shadow_log", sa.Column("replayed_by_log_id", sa.Integer(), nullable=True))
    op.add_column("ekf_shadow_log", sa.Column("supersedes_log_id", sa.Integer(), nullable=True))
    op.add_column("ekf_shadow_log", sa.Column("replay_base_log_id", sa.Integer(), nullable=True))

    # Self-referential provenance links; SET NULL keeps the log immutable if a referent is ever
    # removed (privacy CASCADE deletes whole source lineages, not individual chain rows).
    for col in _LINEAGE_FKS:
        op.create_foreign_key(
            f"ekf_shadow_log_{col}_fkey",
            "ekf_shadow_log",
            "ekf_shadow_log",
            [col],
            ["id"],
            ondelete="SET NULL",
        )

    # a037 can already contain sticky pending corrections. Give each one an initial generation
    # before installing the flag/revision invariant; otherwise (0 == 0, flag=true) would be both
    # invalid and invisible to the replay consumer.
    op.execute(
        """
        UPDATE ekf_shadow_log
           SET correction_revision = 1
         WHERE source_wellness_sample_id IS NOT NULL
           AND event_type = 'update'
           AND correction_requires_replay IS TRUE
        """
    )

    # Re-scope original-wellness uniqueness positively to event_type='update' so replay rows for
    # the same (sample, model) coexist with the original assimilation.
    op.drop_index("uq_ekf_shadow_wellness_sample_model", table_name="ekf_shadow_log")
    op.create_index(
        "uq_ekf_original_wellness_source_model",
        "ekf_shadow_log",
        ["source_wellness_sample_id", "model_version"],
        unique=True,
        postgresql_where=sa.text("source_wellness_sample_id IS NOT NULL AND event_type = 'update'"),
    )
    # Replay idempotency authority: one replay per source observation, per model, per correction
    # generation. Keyed by source identity (not lineage), so retries under a shifting head cannot
    # admit a second replay for the same generation.
    op.create_index(
        "uq_ekf_wellness_replay_revision",
        "ekf_shadow_log",
        ["source_wellness_sample_id", "model_version", "correction_revision"],
        unique=True,
        postgresql_where=sa.text("source_wellness_sample_id IS NOT NULL AND event_type = 'replay'"),
    )
    # A replay row must carry complete lineage provenance.
    op.create_check_constraint(
        "ck_ekf_replay_lineage_complete",
        "ekf_shadow_log",
        "event_type <> 'replay' OR ("
        "source_wellness_sample_id IS NOT NULL AND supersedes_log_id IS NOT NULL "
        "AND replay_base_log_id IS NOT NULL AND correction_revision > 0)",
    )
    # replayed_revision tracks correction_revision monotonically and never exceeds it
    # (correction_requires_replay ⇔ replayed_revision < correction_revision).
    op.create_check_constraint(
        "ck_ekf_replayed_revision_bounds",
        "ekf_shadow_log",
        "replayed_revision >= 0 AND replayed_revision <= correction_revision",
    )
    # On the mutable ORIGINAL assimilation row, the sticky flag exactly materializes whether a
    # correction generation remains unreplayed. Replay rows carry their own revision but are not
    # reconciliation records, so the equivalence is intentionally scoped to source-backed updates.
    op.create_check_constraint(
        "ck_ekf_original_replay_flag_consistent",
        "ekf_shadow_log",
        "event_type <> 'update' OR source_wellness_sample_id IS NULL OR "
        "correction_requires_replay = (replayed_revision < correction_revision)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ekf_original_replay_flag_consistent", "ekf_shadow_log", type_="check"
    )
    op.drop_constraint("ck_ekf_replayed_revision_bounds", "ekf_shadow_log", type_="check")
    op.drop_constraint("ck_ekf_replay_lineage_complete", "ekf_shadow_log", type_="check")
    op.drop_index("uq_ekf_wellness_replay_revision", table_name="ekf_shadow_log")
    op.drop_index("uq_ekf_original_wellness_source_model", table_name="ekf_shadow_log")

    # Replay rows are a new-schema concept the old (source, model) unique index cannot represent
    # (they share (sample, model) with their original). Removing the feature removes its rows —
    # shadow-only data, no decision impact — so the pre-replay unique index can be recreated.
    op.execute("DELETE FROM ekf_shadow_log WHERE event_type = 'replay'")
    op.create_index(
        "uq_ekf_shadow_wellness_sample_model",
        "ekf_shadow_log",
        ["source_wellness_sample_id", "model_version"],
        unique=True,
        postgresql_where=sa.text("source_wellness_sample_id IS NOT NULL"),
    )

    for col in reversed(_LINEAGE_FKS):
        op.drop_constraint(f"ekf_shadow_log_{col}_fkey", "ekf_shadow_log", type_="foreignkey")
    for col in ("replay_base_log_id", "supersedes_log_id", "replayed_by_log_id", "replayed_at",
                "replayed_revision", "correction_revision"):
        op.drop_column("ekf_shadow_log", col)
