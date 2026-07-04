"""Create objectives table (Phase 4a — goal-anchored program).

Revision ID: a010_objectives
Revises: a009_block_session_prefs
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a010_objectives"
down_revision: str | None = "a009_block_session_prefs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "benchmark_code",
            sa.String(length=100),
            sa.ForeignKey("benchmark_definitions.code"),
            nullable=True,
        ),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=True),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column("target_unit", sa.String(length=50), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "status",
            sa.Enum("active", "achieved", "abandoned", name="objectivestatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_objectives_id", "objectives", ["id"], unique=False)
    op.create_index("ix_objectives_user_id", "objectives", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_objectives_user_id", table_name="objectives")
    op.drop_index("ix_objectives_id", table_name="objectives")
    op.drop_table("objectives")
    sa.Enum(name="objectivestatus").drop(op.get_bind())
