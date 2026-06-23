"""Daily wellness samples (readiness input, PDR-0005).

Adds the wellness_samples table: one acute daily-wellness row per athlete per
date per source, feeding the backend-owned readiness scalar.

Revision ID: a004_wellness
Revises: a003_session_orig_date
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a004_wellness"
down_revision: str | None = "a003_session_orig_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wellness_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("hrv_ms", sa.Float(), nullable=True),
        sa.Column("sleep_hours", sa.Float(), nullable=True),
        sa.Column("sleep_quality", sa.Float(), nullable=True),
        sa.Column("resting_hr", sa.Float(), nullable=True),
        sa.Column("soreness", sa.Float(), nullable=True),
        sa.Column("mood", sa.Float(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "date", "source", name="uq_wellness_user_date_source"
        ),
    )
    op.create_index(
        op.f("ix_wellness_samples_user_id"), "wellness_samples", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_wellness_samples_date"), "wellness_samples", ["date"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_wellness_samples_date"), table_name="wellness_samples")
    op.drop_index(op.f("ix_wellness_samples_user_id"), table_name="wellness_samples")
    op.drop_table("wellness_samples")
