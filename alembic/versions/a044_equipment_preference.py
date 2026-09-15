"""Equipment preference on the athlete profile (S-C).

``athlete_profiles.equipment_preference`` holds what an athlete prefers to train with —
``barbell``, ``dumbbell``, ``machine`` (machines include cables) — and ``{}`` for no preference.
It is a tie-break among movements the athlete can already do, and is deliberately a separate
column from ``equipment``, which says what they HAVE and filters selection: a preference must
never be able to exclude or admit a movement (docs/PRESCRIBER_LOGIC.md).

Every existing profile gets ``{}`` — no preference — so selection is unchanged until an athlete
sets one.

Revision ID: a044_equipment_preference
Revises: a043_benchmark_description
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a044_equipment_preference"
down_revision: str | None = "a043_benchmark_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "athlete_profiles",
        sa.Column(
            "equipment_preference",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("athlete_profiles", "equipment_preference")
