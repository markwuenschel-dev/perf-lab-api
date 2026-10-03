"""Add the final safety outcome to prescription_decisions (W1-c).

``chosen_candidate_id`` is the RANKED winner. Finalize can replace that session for safety
(a hard violation, or a hard rule that could not be evaluated → complete rest), and the
decision row used to record only the ranking — a zero-minute Rest looked like a normal
"hyp_upper_split" decision. These columns record what was actually prescribed, kept apart
from the ranking evidence. Nullable: rows written before this revision carry NULL (unknown),
never a guessed value.

Revision ID: a050_decision_final_outcome
Revises: a049_objective_display_rank
Create Date: 2026-10-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a050_decision_final_outcome"
down_revision: str | None = "a049_objective_display_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "prescription_decisions"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("final_outcome", sa.String(length=32), nullable=True))
    op.add_column(_TABLE, sa.Column("final_prescription_type", sa.String(), nullable=True))
    op.add_column(_TABLE, sa.Column("final_duration_min", sa.Integer(), nullable=True))
    op.add_column(
        _TABLE, sa.Column("hard_violations_json", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("unevaluated_hard_json", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    for column in (
        "unevaluated_hard_json",
        "hard_violations_json",
        "final_duration_min",
        "final_prescription_type",
        "final_outcome",
    ):
        op.drop_column(_TABLE, column)
