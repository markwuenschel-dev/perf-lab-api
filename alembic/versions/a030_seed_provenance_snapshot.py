"""Immutable per-axis seed provenance snapshot on athlete_profiles (ADR-0059).

Additive, nullable. The snapshot records how each capacity axis was seeded (source,
evidence tier, seed variance) at onboarding — immutable provenance, never read at
runtime for current provisionality (the live CapacityConfidence is the sole runtime
authority). The P7 scalar initial_seed_status/initial_seed_confidence become derived
analytics rollups over this snapshot.

Conservative backfill: existing profiles predate tiered seeding, so their snapshot
stays NULL and initial_seed_status keeps its P7 value — no seed confidence is
fabricated from current state (legacy → NULL / unknown).

Revision ID: a030_seed_provenance_snapshot
Revises: a029_capacity_floor_shadow_log
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a030_seed_provenance_snapshot"
down_revision: str | None = "a029_capacity_floor_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "athlete_profiles"


def upgrade() -> None:
    op.add_column(
        _T,
        sa.Column(
            "initial_seed_by_axis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Immutable per-axis seed provenance (ADR-0059); never runtime-read",
        ),
    )
    op.add_column(_T, sa.Column("seed_policy_version", sa.String(length=40), nullable=True))
    op.add_column(_T, sa.Column("seeded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column(_T, "seeded_at")
    op.drop_column(_T, "seed_policy_version")
    op.drop_column(_T, "initial_seed_by_axis")
