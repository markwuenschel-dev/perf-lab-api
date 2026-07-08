"""Add a `stress` signal to wellness_samples (P8, ADR-0053).

Stress is core subjective-readiness data (same family as mood/soreness/sleep),
device-free, and fits the implicit-tracking model. Additive + nullable, so it
does not disturb existing rows or the shadow services that read specific fields.
0–10 scale, higher = worse. See app/logic/wellness_signals.py::SIGNAL_CONFIG.

Revision ID: a023_add_wellness_stress
Revises: a022_athlete_onboarding_state
Create Date: 2026-07-08
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a023_add_wellness_stress"
down_revision: str | None = "a022_athlete_onboarding_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wellness_samples",
        sa.Column("stress", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wellness_samples", "stress")
