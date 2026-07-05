"""Create ekf_shadow_log table (shadow full-covariance EKF telemetry, ADR-0041).

Additive, capture-only table: one row per EKF predict/update step in the parallel
shadow estimator. No existing table is touched; nothing here affects production state.

Revision ID: a013_ekf_shadow_log
Revises: a012_recovery_shadow_log
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a013_ekf_shadow_log"
down_revision: str | None = "a012_recovery_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ekf_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("belief_at", sa.DateTime(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("mean_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("variance_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("covariance_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("benchmark_code", sa.String(length=80), nullable=True),
        sa.Column("innovation", sa.Float(), nullable=True),
        sa.Column("gain_norm", sa.Float(), nullable=True),
        sa.Column("trace_pre", sa.Float(), nullable=True),
        sa.Column("trace_post", sa.Float(), nullable=True),
        sa.Column("nis", sa.Float(), nullable=True),
        sa.Column("n_obs", sa.Integer(), nullable=True),
        sa.Column("decision_impact", sa.String(length=40), nullable=False, server_default="none_shadow_only"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ekf_shadow_log_id", "ekf_shadow_log", ["id"], unique=False)
    op.create_index("ix_ekf_shadow_log_user_id", "ekf_shadow_log", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ekf_shadow_log_user_id", table_name="ekf_shadow_log")
    op.drop_index("ix_ekf_shadow_log_id", table_name="ekf_shadow_log")
    op.drop_table("ekf_shadow_log")
