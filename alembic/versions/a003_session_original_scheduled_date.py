"""Add original_scheduled_date to planned_sessions.

Tracks the original plan date when a session is rescheduled, so the first move
is recoverable and reschedules can be told apart from on-plan sessions.

Revision ID: a003_session_orig_date
Revises: a002_planned_bench_cols
Create Date: 2026-06-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a003_session_orig_date"
down_revision: str | None = "a002_planned_bench_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS so mixed local DB states can migrate safely (matches a002).
    op.execute(
        "ALTER TABLE planned_sessions "
        "ADD COLUMN IF NOT EXISTS original_scheduled_date DATE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE planned_sessions DROP COLUMN IF EXISTS original_scheduled_date"
    )
