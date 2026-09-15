"""What a benchmark measures, as its own field (S-B, Assess help).

``benchmark_definitions.description`` says what a benchmark measures. It is deliberately a
separate column from ``protocol_summary``, which says how to measure it: an explanation of a
metric and the instructions for performing it are different things, and the Assess card shows
them in separate sections.

The text is code-owned. ``app.scripts.seed_benchmarks.BENCHMARK_EXPLANATIONS`` is the source,
and the seed's enrichment pass writes it — ``description`` and ``protocol_summary`` only — on
every catalog seed, including rows seeded before this column existed. This migration adds the
column and writes nothing: the seed owns the content.

Revision ID: a043_benchmark_description
Revises: a042_evidence_performed_at
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a043_benchmark_description"
down_revision: str | None = "a042_evidence_performed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("benchmark_definitions", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("benchmark_definitions", "description")
