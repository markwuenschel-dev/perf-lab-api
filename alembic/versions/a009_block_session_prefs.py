"""Add per-block session preferences to mesocycle_blocks.

Revision ID: a009_block_session_prefs
Revises: a008_profile_primary_goal
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a009_block_session_prefs"
down_revision: str | None = "a008_profile_primary_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mesocycle_blocks",
        sa.Column("target_session_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "mesocycle_blocks",
        sa.Column("accessory_emphasis", sa.Text(), nullable=True),
    )
    op.add_column(
        "mesocycle_blocks",
        sa.Column("accessory_focus", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mesocycle_blocks", "accessory_focus")
    op.drop_column("mesocycle_blocks", "accessory_emphasis")
    op.drop_column("mesocycle_blocks", "target_session_minutes")
