"""Add primary_goal to athlete_profiles.

Revision ID: a008_profile_primary_goal
Revises: a007_athlete_display_name
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a008_profile_primary_goal"
down_revision: str | None = "a007_athlete_display_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "athlete_profiles", sa.Column("primary_goal", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("athlete_profiles", "primary_goal")
