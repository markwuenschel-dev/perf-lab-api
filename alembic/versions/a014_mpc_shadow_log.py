"""Create mpc_shadow_log table (shadow MPC planner telemetry, ADR-0042).

Additive, capture-only table: one row per prescription recording MPC-choice vs
greedy-choice + per-candidate objective breakdown. No existing table is touched; nothing
here affects a prescription or production state.

Revision ID: a014_mpc_shadow_log
Revises: a013_ekf_shadow_log
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a014_mpc_shadow_log"
down_revision: str | None = "a013_ekf_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mpc_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("goal", sa.String(length=80), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("greedy_branch_id", sa.String(length=120), nullable=True),
        sa.Column("greedy_type", sa.String(length=120), nullable=True),
        sa.Column("mpc_branch_id", sa.String(length=120), nullable=True),
        sa.Column("mpc_type", sa.String(length=120), nullable=True),
        sa.Column("agreement", sa.Boolean(), nullable=False),
        sa.Column("belief_trace", sa.Float(), nullable=True),
        sa.Column("candidate_scores_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weights_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False, server_default="none_shadow_only"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mpc_shadow_log_id", "mpc_shadow_log", ["id"], unique=False)
    op.create_index("ix_mpc_shadow_log_user_id", "mpc_shadow_log", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mpc_shadow_log_user_id", table_name="mpc_shadow_log")
    op.drop_index("ix_mpc_shadow_log_id", table_name="mpc_shadow_log")
    op.drop_table("mpc_shadow_log")
