"""Wellness ingest tokens: a personal, write-only credential for phone-pushed wellness.

Apple Watch data can only leave the iPhone through something running on it (an iOS Shortcut
first). ``wellness_ingest_tokens`` holds a SHA-256 of each token, never the token itself; a
token may only call ``POST /v1/wellness/ingest`` for its athlete. See
``app/models/wellness_ingest_token.py``.

Revision ID: a048_wellness_ingest_tokens
Revises: a047_dose_shadow_context
Create Date: 2026-09-28
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a048_wellness_ingest_tokens"
down_revision: str | None = "a047_dose_shadow_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "wellness_ingest_tokens"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_wellness_ingest_tokens_id", _TABLE, ["id"], unique=False)
    op.create_index("ix_wellness_ingest_tokens_user_id", _TABLE, ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_wellness_ingest_tokens_user_id", table_name=_TABLE)
    op.drop_index("ix_wellness_ingest_tokens_id", table_name=_TABLE)
    op.drop_table(_TABLE)
