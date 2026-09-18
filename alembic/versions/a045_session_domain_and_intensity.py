"""A planned session's own domain, and the block's workload preference (D + E).

``planned_sessions.domain`` holds the CANONICAL domain the slot was generated from. The slot's
``modality`` cannot stand in for it: ``_DOMAIN_SLOT`` (app/services/planning_service.py:82) maps
powerlifting→Strength, weightlifting→Power and gymnastics→Calisthenics, so several domains share
one modality label and canonicalizing it back cannot recover which one was meant. Without this
column a multi-style block could only relabel days — the prescriber would still build every
session from the block goal.

``mesocycle_blocks.intensity`` holds an explicit workload preference: ``easy`` | ``medium`` | ``hard``.
NULL means medium, so every existing block keeps exactly today's behaviour. It shifts targets
inside the periodization envelope; it never loosens a safety override.

Both columns are nullable with no backfill, on purpose: an existing planned session has no
recorded domain, and inventing one would change what it prescribes.

Revision ID: a045_session_domain_intensity
Revises: a044_equipment_preference
Create Date: 2026-09-18
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a045_session_domain_intensity"
down_revision: str | None = "a044_equipment_preference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "planned_sessions",
        # No column comment: the drift guard compares comments too, and the model carries it.
        sa.Column("domain", sa.String(), nullable=True),
    )
    op.add_column(
        "mesocycle_blocks",
        # sa.Text to match the model column (MesocycleBlock.intensity), and no comment:
        # tests/test_migration_model_drift.py compares both, and the model carries the prose.
        sa.Column("intensity", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mesocycle_blocks", "intensity")
    op.drop_column("planned_sessions", "domain")
