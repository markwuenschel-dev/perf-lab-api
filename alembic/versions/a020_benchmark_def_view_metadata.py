"""Add skill-state view metadata to benchmark_definitions (Wave 2 P7, ADR-0046/0047).

Four nullable columns enriching a definition for domain-filtered skill-state
projection — they add view metadata, not new state axes. See
app/models/benchmark_definition.py.

Revision ID: a020_benchmark_def_view_metadata
Revises: a019_workout_set_logs
Create Date: 2026-07-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a020_benchmark_def_view_metadata"
down_revision: str | None = "a019_workout_set_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_definitions",
        sa.Column("domain_lenses", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "benchmark_definitions",
        sa.Column("movement_skill_mappings", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "benchmark_definitions",
        sa.Column("assessable_skill_tags", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "benchmark_definitions",
        sa.Column("measurement_protocol", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_definitions", "measurement_protocol")
    op.drop_column("benchmark_definitions", "assessable_skill_tags")
    op.drop_column("benchmark_definitions", "movement_skill_mappings")
    op.drop_column("benchmark_definitions", "domain_lenses")
