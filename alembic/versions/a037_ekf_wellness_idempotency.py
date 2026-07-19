"""AUD-C8: wellness-observation idempotency on ekf_shadow_log.

The shadow EKF re-assimilated a wellness reading on every re-POST — an idempotent wellness
upsert (client retry) re-ran the belief update and shrank variance again, overconfidence that
corrupts the very evidence a future promotion would rest on (ADR-0041 was silent on re-ingest).

This adds the source identity + content-hash provenance + the **partial unique index** that is
the database concurrency authority for at-most-once assimilation per
(wellness observation, model version). All new columns are nullable: existing rows and
predict/benchmark rows carry no wellness source, so no backfill is invented; a CHECK keeps a
*linked* row's hash provenance complete. The FK CASCADEs like the sibling shadow tables
(capacity_floor/dose_routing/strength_decline -> their source), so a privacy deletion of the
wellness sample removes its derived shadow rows.

Column shapes are inlined; migrations stay replayable independent of app refactors.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a037_ekf_wellness_idempotency"
down_revision: str | None = "a036_athlete_states_notnull"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ekf_shadow_log", sa.Column("source_wellness_sample_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "ekf_shadow_log", sa.Column("assimilated_content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ekf_shadow_log", sa.Column("latest_seen_content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ekf_shadow_log",
        sa.Column(
            "correction_requires_replay",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ekf_shadow_log", sa.Column("correction_detected_at", sa.DateTime(), nullable=True)
    )

    op.create_index(
        "ix_ekf_shadow_log_source_wellness_sample_id",
        "ekf_shadow_log",
        ["source_wellness_sample_id"],
    )
    op.create_foreign_key(
        "ekf_shadow_log_source_wellness_sample_id_fkey",
        "ekf_shadow_log",
        "wellness_samples",
        ["source_wellness_sample_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # Partial unique index — the idempotency concurrency authority. Legacy/predict/benchmark
    # rows have a NULL source id and are unrestricted.
    op.create_index(
        "uq_ekf_shadow_wellness_sample_model",
        "ekf_shadow_log",
        ["source_wellness_sample_id", "model_version"],
        unique=True,
        postgresql_where=sa.text("source_wellness_sample_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_ekf_shadow_wellness_hash_complete",
        "ekf_shadow_log",
        "source_wellness_sample_id IS NULL "
        "OR (assimilated_content_hash IS NOT NULL AND latest_seen_content_hash IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ekf_shadow_wellness_hash_complete", "ekf_shadow_log", type_="check"
    )
    op.drop_index("uq_ekf_shadow_wellness_sample_model", table_name="ekf_shadow_log")
    op.drop_constraint(
        "ekf_shadow_log_source_wellness_sample_id_fkey", "ekf_shadow_log", type_="foreignkey"
    )
    op.drop_index("ix_ekf_shadow_log_source_wellness_sample_id", table_name="ekf_shadow_log")
    op.drop_column("ekf_shadow_log", "correction_detected_at")
    op.drop_column("ekf_shadow_log", "correction_requires_replay")
    op.drop_column("ekf_shadow_log", "latest_seen_content_hash")
    op.drop_column("ekf_shadow_log", "assimilated_content_hash")
    op.drop_column("ekf_shadow_log", "source_wellness_sample_id")
