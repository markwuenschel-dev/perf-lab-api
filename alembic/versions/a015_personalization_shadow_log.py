"""Create personalization_shadow_log table (per-athlete θ_i shadow telemetry, ADR-0043).

Additive, capture-only table: one row per wellness ingest with a personalization estimate,
recording population vs personalized clearance multipliers + shrinkage/uncertainty. No
existing table is touched; nothing here affects a prescription or production state.

Revision ID: a015_personalization_shadow_log
Revises: a014_mpc_shadow_log
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a015_personalization_shadow_log"
down_revision: str | None = "a014_mpc_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personalization_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("parameter", sa.String(length=40), nullable=False, server_default="recovery_beta"),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("n_obs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shrinkage_weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("theta_trace", sa.Float(), nullable=False, server_default="0"),
        sa.Column("wellness", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("population_multiplier", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("personalized_multiplier", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False, server_default="none_shadow_only"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_personalization_shadow_log_id", "personalization_shadow_log", ["id"], unique=False)
    op.create_index("ix_personalization_shadow_log_user_id", "personalization_shadow_log", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_personalization_shadow_log_user_id", table_name="personalization_shadow_log")
    op.drop_index("ix_personalization_shadow_log_id", table_name="personalization_shadow_log")
    op.drop_table("personalization_shadow_log")
