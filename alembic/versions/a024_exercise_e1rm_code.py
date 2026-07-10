"""Add exercises.e1rm_benchmark_code (Wave 2 P9 — strength loop, ADR-0045).

Links a catalog lift to its estimated-1RM benchmark definition so a logged top
set can emit an e1RM observation and a prescribed lift can resolve %e1RM → kg.
Nullable — only the barbell lifts with a seeded ``pl_e1rm_*`` anchor carry it.
See app/models/exercise.py and app/logic/strength_calibration.py.

Revision ID: a024_exercise_e1rm_code
Revises: a023_add_wellness_stress
Create Date: 2026-07-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a024_exercise_e1rm_code"
down_revision: str | None = "a023_add_wellness_stress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exercises",
        sa.Column("e1rm_benchmark_code", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exercises", "e1rm_benchmark_code")
