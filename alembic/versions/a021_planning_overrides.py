"""Create planning_overrides table (Wave 2 P7 — user-owned structure, ADR-0051).

User-declared constraints on the plan (pin modality mix / goal / phase,
min/max frequency, include/exclude modality, movement preference), each with a
hard_user_override or soft_user_preference authority. Applied in the planner
pipeline after the objective blend and floors, before safety/confidence gates.
See app/models/planning_override.py.

Revision ID: a021_planning_overrides
Revises: a020_benchmark_def_view_metadata
Create Date: 2026-07-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a021_planning_overrides"
down_revision: str | None = "a020_benchmark_def_view_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("override_type", sa.String(length=40), nullable=False),
        sa.Column(
            "authority",
            sa.String(length=20),
            nullable=False,
            server_default="hard_user_override",
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_overrides_id", "planning_overrides", ["id"], unique=False)
    op.create_index(
        "ix_planning_overrides_user_id", "planning_overrides", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_planning_overrides_user_id", table_name="planning_overrides")
    op.drop_index("ix_planning_overrides_id", table_name="planning_overrides")
    op.drop_table("planning_overrides")
