"""Create workout_set_logs table (Wave 2 P7 — per-set workout logging, ADR-0045).

The set is the atomic logged unit. A workout_logs row is the session header;
its sets live here as queryable child rows bound to catalog exercises. The
exercise's load_type types which fields are meaningful. See
app/models/workout_set_log.py.

Revision ID: a019_workout_set_logs
Revises: a018_wearable_connections
Create Date: 2026-07-07
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a019_workout_set_logs"
down_revision: str | None = "a018_wearable_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workout_set_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "workout_log_id",
            sa.Integer(),
            sa.ForeignKey("workout_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column(
            "exercise_id",
            sa.Integer(),
            sa.ForeignKey("exercises.id"),
            nullable=True,
        ),
        sa.Column("free_text_name", sa.String(), nullable=True),
        sa.Column("load_type", sa.String(), nullable=True),
        sa.Column("load_kg", sa.Float(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("rir", sa.Float(), nullable=True),
        sa.Column("is_top_set", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("band", sa.String(), nullable=True),
        sa.Column("elevation", sa.String(), nullable=True),
        sa.Column("tempo", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_set_logs_id", "workout_set_logs", ["id"], unique=False)
    op.create_index(
        "ix_workout_set_logs_workout_log_id",
        "workout_set_logs",
        ["workout_log_id"],
        unique=False,
    )
    op.create_index(
        "ix_workout_set_logs_exercise_id",
        "workout_set_logs",
        ["exercise_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workout_set_logs_exercise_id", table_name="workout_set_logs")
    op.drop_index("ix_workout_set_logs_workout_log_id", table_name="workout_set_logs")
    op.drop_index("ix_workout_set_logs_id", table_name="workout_set_logs")
    op.drop_table("workout_set_logs")
