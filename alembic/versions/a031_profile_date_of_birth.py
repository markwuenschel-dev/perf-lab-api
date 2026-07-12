"""Add date_of_birth to athlete_profiles (PDR-0010 age safety basic).

Additive, nullable. Age is a safety input; a plausible DOB is a required onboarding
basic and being a minor is a flagged limitation (never a hard lock). Existing profiles
keep NULL until re-onboarded/edited — they simply show `date_of_birth` in
`missing_basics`.

Revision ID: a031_profile_date_of_birth
Revises: a030_seed_provenance_snapshot
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a031_profile_date_of_birth"
down_revision: str | None = "a030_seed_provenance_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("athlete_profiles", sa.Column("date_of_birth", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("athlete_profiles", "date_of_birth")
