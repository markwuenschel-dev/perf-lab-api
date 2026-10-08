"""Benchmark state disposition (P3-pre): late observations are record-only.

A benchmark observation with ``observed_at`` earlier than the athlete's current state head
used to be applied to the head and stamped with its past time, so the new row sorted below the
head and its effect was lost. This is the same bug P3a fixed for workouts. It is now recorded
without a state update, and says so:

* ``benchmark_observations.state_disposition``:
  - ``applied``: the state effect was evaluated against the head, in time order. A row is
    written only if capacity changed.
  - ``record_only``: not evaluated, for chronology.
  - NULL: the observation carried no state authority, or predates this release.
* ``benchmark_observations.state_disposition_reason``: why a row is record-only. The values are
  the same as for workouts (``event_before_current_state``, ``current_state_in_future``).

The downgrade drops the columns.

Revision ID: a054_benchmark_state_disposition
Revises: a053_workout_state_disposition
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a054_benchmark_state_disposition"
down_revision: str | None = "a053_workout_state_disposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("benchmark_observations", sa.Column("state_disposition", sa.String(), nullable=True))
    op.add_column(
        "benchmark_observations", sa.Column("state_disposition_reason", sa.String(), nullable=True)
    )
    op.create_check_constraint(
        "ck_benchmark_observations_state_disposition",
        "benchmark_observations",
        "state_disposition IS NULL OR state_disposition IN ('applied', 'record_only')",
    )
    op.create_check_constraint(
        "ck_benchmark_observations_record_only_has_reason",
        "benchmark_observations",
        "state_disposition IS DISTINCT FROM 'record_only' OR state_disposition_reason IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_observations_record_only_has_reason", "benchmark_observations", type_="check"
    )
    op.drop_constraint(
        "ck_benchmark_observations_state_disposition", "benchmark_observations", type_="check"
    )
    op.drop_column("benchmark_observations", "state_disposition_reason")
    op.drop_column("benchmark_observations", "state_disposition")
