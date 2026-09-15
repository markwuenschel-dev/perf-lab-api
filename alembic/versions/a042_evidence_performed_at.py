"""Performance time for strength evidence; effort provenance on set rows (S2).

``benchmark_observations.performed_at`` is when the lift was performed. It is nullable, and
NULL stays unknown: prescription freshness (``app.logic.prescription_evidence``) reads this
column, so it must never be filled from a submission or migration timestamp.

The one backfill is exact rather than inferred. Workout extraction has written the workout's
own timestamp as ``observed_at`` since P9 (ae8edf9: ``_extract_e1rm_observations(..., log_ts)``),
so for ``source = 'workout_extraction'`` rows ``observed_at`` IS the performance time. Every
other row stays NULL: for athlete entries ``observed_at`` is often the submission time, and
legacy and corpus rows carry no trustworthy performance date.

What this migration deliberately does NOT do: rewrite ``affects_prescription`` on historical
extraction rows. Before S2 a gated set below the all-time watermark was written ``false``, so
equally characterized historical non-PR rows can stay excluded from prescription until they
leave the freshness window. That is an accepted, conservative transitional limitation.

``workout_set_logs.effort_fidelity`` / ``entry_group_id`` persist the effort provenance ingest
already infers, so a set can be reprocessed later. Historical rows stay NULL: it was never
recorded, and inferring it now would state something nobody observed.

Revision ID: a042_evidence_performed_at
Revises: a041_prescription_basis_nulls
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a042_evidence_performed_at"
down_revision: str | None = "a041_prescription_basis_nulls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_observations", sa.Column("performed_at", sa.DateTime(), nullable=True)
    )
    op.execute(
        """
        UPDATE benchmark_observations
        SET performed_at = observed_at
        WHERE source = 'workout_extraction' AND performed_at IS NULL
        """
    )
    op.add_column(
        "workout_set_logs", sa.Column("effort_fidelity", sa.String(length=20), nullable=True)
    )
    op.add_column("workout_set_logs", sa.Column("entry_group_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("workout_set_logs", "entry_group_id")
    op.drop_column("workout_set_logs", "effort_fidelity")
    op.drop_column("benchmark_observations", "performed_at")
