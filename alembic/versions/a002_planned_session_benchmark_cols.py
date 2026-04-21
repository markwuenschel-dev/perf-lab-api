"""Add benchmark columns to planned_sessions.

Revision ID: a002_planned_bench_cols
Revises: a001_benchmark_kpi
Create Date: 2026-04-21
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a002_planned_bench_cols"
down_revision: Union[str, None] = "a001_benchmark_kpi"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use IF NOT EXISTS so mixed local DB states can migrate safely.
    op.execute(
        "ALTER TABLE planned_sessions "
        "ADD COLUMN IF NOT EXISTS is_benchmark BOOLEAN DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE planned_sessions "
        "ADD COLUMN IF NOT EXISTS benchmark_key VARCHAR"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE planned_sessions DROP COLUMN IF EXISTS benchmark_key")
    op.execute("ALTER TABLE planned_sessions DROP COLUMN IF EXISTS is_benchmark")
