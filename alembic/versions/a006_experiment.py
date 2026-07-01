"""Experiment assignment table for adaptive vs static arm comparison.

Revision ID: a006_experiment
Revises: a005_telemetry
Create Date: 2026-06-30
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a006_experiment"
down_revision: str | None = "a005_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("experiment_name", sa.String(), nullable=False),
        sa.Column("arm", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiment_assignments_user_id", "experiment_assignments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_experiment_assignments_user_id", table_name="experiment_assignments")
    op.drop_table("experiment_assignments")
