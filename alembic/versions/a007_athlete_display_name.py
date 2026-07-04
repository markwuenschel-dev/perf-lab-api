"""Add display_name to athlete_profiles.

Revision ID: a007_athlete_display_name
Revises: a006_experiment
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a007_athlete_display_name"
down_revision: str | None = "a006_experiment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "athlete_profiles", sa.Column("display_name", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("athlete_profiles", "display_name")
