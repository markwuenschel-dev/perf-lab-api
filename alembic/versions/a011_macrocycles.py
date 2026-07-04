"""Create macrocycles table + link blocks to it (Phase 5 — goal-anchored program).

Adds the thin ``macrocycles`` program container (ADR-0040) and a nullable
``mesocycle_blocks.macrocycle_id`` FK so a block can hang under a macrocycle
without breaking existing block rows.

Revision ID: a011_macrocycles
Revises: a010_objectives
Create Date: 2026-07-04
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a011_macrocycles"
down_revision: str | None = "a010_objectives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macrocycles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "objective_id",
            sa.Integer(),
            sa.ForeignKey("objectives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "achieved", "abandoned", name="macrocyclestatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_macrocycles_id", "macrocycles", ["id"], unique=False)
    op.create_index("ix_macrocycles_user_id", "macrocycles", ["user_id"], unique=False)
    op.create_index(
        "ix_macrocycles_objective_id", "macrocycles", ["objective_id"], unique=False
    )

    # Link blocks to their macrocycle. Nullable → existing blocks are unaffected;
    # ON DELETE SET NULL → deleting a macrocycle detaches its blocks, never deletes them.
    op.add_column(
        "mesocycle_blocks",
        sa.Column(
            "macrocycle_id",
            sa.Integer(),
            sa.ForeignKey("macrocycles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_mesocycle_blocks_macrocycle_id",
        "mesocycle_blocks",
        ["macrocycle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mesocycle_blocks_macrocycle_id", table_name="mesocycle_blocks")
    op.drop_column("mesocycle_blocks", "macrocycle_id")

    op.drop_index("ix_macrocycles_objective_id", table_name="macrocycles")
    op.drop_index("ix_macrocycles_user_id", table_name="macrocycles")
    op.drop_index("ix_macrocycles_id", table_name="macrocycles")
    op.drop_table("macrocycles")
    sa.Enum(name="macrocyclestatus").drop(op.get_bind())
