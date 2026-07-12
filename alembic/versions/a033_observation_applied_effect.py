"""Add applied_capacity_effect + decline_transition_status to benchmark_observations (INT-02, ADR-0066).

Additive, nullable. Records the RESOLVED-vs-APPLIED distinction on the observation:
`capacity_effect` is the resolved authority (ADR-0058); `applied_capacity_effect` is
what the downward-decline transition policy actually exercised (e.g. `none` on a first
material low observation whose resolved authority is `bidirectional_update`).
`decline_transition_status` records the state-machine outcome for that observation.

Revision ID: a033_observation_applied_effect
Revises: a032_strength_decline_candidates
Create Date: 2026-07-12
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a033_observation_applied_effect"
down_revision: str | None = "a032_strength_decline_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_observations",
        sa.Column("applied_capacity_effect", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "benchmark_observations",
        sa.Column("decline_transition_status", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_observations", "decline_transition_status")
    op.drop_column("benchmark_observations", "applied_capacity_effect")
