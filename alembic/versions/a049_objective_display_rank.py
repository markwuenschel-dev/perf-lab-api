"""Add objectives.display_rank — the athlete's display order for objectives.

Display only — not a weight (ADR-0061: ``priority`` stays the only weighting
mechanism). Written 1..N by ``PUT /v1/objectives/order``; NULL for objectives never
ordered, which sort last.

Revision ID: a049_objective_display_rank
Revises: a048_wellness_ingest_tokens
Create Date: 2026-09-28
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a049_objective_display_rank"
down_revision: str | None = "a048_wellness_ingest_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("objectives", sa.Column("display_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("objectives", "display_rank")
