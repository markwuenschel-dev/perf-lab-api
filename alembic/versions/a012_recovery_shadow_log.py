"""Create recovery_shadow_log table (Q2 recovery priors — shadow telemetry).

Additive, capture-only table: baseline vs learned fatigue-clearance multipliers per
wellness ingest. No existing table touched.

Revision ID: a012_recovery_shadow_log
Revises: a011_macrocycles
Create Date: 2026-07-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a012_recovery_shadow_log"
down_revision: str | None = "a011_macrocycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recovery_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("wellness", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fatigue_before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("baseline_clearance_multiplier", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("learned_clearance_multiplier", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False, server_default="none_shadow_only"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_shadow_log_id", "recovery_shadow_log", ["id"], unique=False)
    op.create_index(
        "ix_recovery_shadow_log_user_id", "recovery_shadow_log", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_recovery_shadow_log_user_id", table_name="recovery_shadow_log")
    op.drop_index("ix_recovery_shadow_log_id", table_name="recovery_shadow_log")
    op.drop_table("recovery_shadow_log")
