"""Add onboarding state + wellness-tracking prefs to athlete_profiles (P7).

Onboarding state machine (PDR-0010) so the app can resume onboarding and mark a
provisional seed, plus a per-user list of untracked wellness signals (ADR-0049)
so a missing-but-tracked signal stays an honest gap rather than an imputed value.
Server defaults backfill existing rows. See app/models/user.py::AthleteProfile.

Revision ID: a022_athlete_onboarding_state
Revises: a021_planning_overrides
Create Date: 2026-07-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a022_athlete_onboarding_state"
down_revision: str | None = "a021_planning_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "athlete_profiles",
        sa.Column(
            "onboarding_status",
            sa.String(),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column("completed_reason", sa.String(), nullable=True),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column(
            "initial_seed_status",
            sa.String(),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column("initial_seed_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column(
            "untracked_wellness_signals",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("athlete_profiles", "untracked_wellness_signals")
    op.drop_column("athlete_profiles", "initial_seed_confidence")
    op.drop_column("athlete_profiles", "initial_seed_status")
    op.drop_column("athlete_profiles", "completed_reason")
    op.drop_column("athlete_profiles", "onboarding_status")
