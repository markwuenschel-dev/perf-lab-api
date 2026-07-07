"""Create wearable_connections table (Phase 2 — Oura wearable sync).

Per-athlete OAuth/PAT credentials for a cloud wearable provider. Tokens are stored
as Fernet ciphertext (``*_enc`` columns); the app never persists them in plaintext.
One connection per (user_id, provider). See app/models/wearable_connection.py.

Revision ID: a018_wearable_connections
Revises: a017_grip_benchmark_mapping
Create Date: 2026-07-06
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a018_wearable_connections"
down_revision: str | None = "a017_grip_benchmark_mapping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wearable_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="oura"),
        sa.Column("auth_type", sa.String(length=10), nullable=False, server_default="oauth"),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("scope", sa.String(length=200), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_wearable_user_provider"),
    )
    op.create_index("ix_wearable_connections_id", "wearable_connections", ["id"], unique=False)
    op.create_index(
        "ix_wearable_connections_user_id", "wearable_connections", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_wearable_connections_user_id", table_name="wearable_connections")
    op.drop_index("ix_wearable_connections_id", table_name="wearable_connections")
    op.drop_table("wearable_connections")
