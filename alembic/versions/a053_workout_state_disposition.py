"""Workout state disposition and timestamp basis (P3a).

A workout logged with a time earlier than the athlete's current state head used to be
applied to the head and then stamped in the past, so it sorted below the head and its
effect was lost. It is now **record-only**: the workout, its sets, its evidence and its
planned-session link are kept, but no state row is written. The decision is recorded on the
workout so the response and every later read can say so.

* ``workout_logs.state_disposition``: ``applied`` or ``record_only``. NULL means a legacy row
  from before this release; P3c's repair detects those.
* ``workout_logs.state_disposition_reason``: why a row is record-only.
* ``workout_logs.timestamp_basis``: ``event_time`` (the client's timestamp is the event) or
  ``server_now`` (a live submission, resolved to server time under the chain lock).
* ``workout_logs.client_timestamp``: the raw timestamp the client sent, UTC-naive, kept for
  audit whichever basis was used.
* ``workout_logs.received_at``: when the request arrived, which is before any lock wait.
* ``athlete_states.source_workout_log_id`` / ``source_observation_id``: the event that wrote
  each new state row, so later detection is exact instead of heuristic.

The downgrade drops the columns. Nothing else depends on them.

Revision ID: a053_workout_state_disposition
Revises: a052_missed_sessions
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a053_workout_state_disposition"
down_revision: str | None = "a052_missed_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workout_logs", sa.Column("state_disposition", sa.String(), nullable=True))
    op.add_column("workout_logs", sa.Column("state_disposition_reason", sa.String(), nullable=True))
    op.add_column("workout_logs", sa.Column("timestamp_basis", sa.String(), nullable=True))
    op.add_column("workout_logs", sa.Column("client_timestamp", sa.DateTime(), nullable=True))
    op.add_column("workout_logs", sa.Column("received_at", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_workout_logs_state_disposition",
        "workout_logs",
        "state_disposition IS NULL OR state_disposition IN ('applied', 'record_only')",
    )
    op.create_check_constraint(
        "ck_workout_logs_record_only_has_reason",
        "workout_logs",
        "state_disposition IS DISTINCT FROM 'record_only' OR state_disposition_reason IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_workout_logs_timestamp_basis",
        "workout_logs",
        "timestamp_basis IS NULL OR timestamp_basis IN ('event_time', 'server_now')",
    )
    op.add_column(
        "athlete_states",
        sa.Column(
            "source_workout_log_id",
            sa.Integer(),
            sa.ForeignKey(
                "workout_logs.id", ondelete="SET NULL", name="fk_athlete_states_source_workout_log_id"
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "athlete_states",
        sa.Column(
            "source_observation_id",
            sa.Integer(),
            sa.ForeignKey(
                "benchmark_observations.id",
                ondelete="SET NULL",
                name="fk_athlete_states_source_observation_id",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("athlete_states", "source_observation_id")
    op.drop_column("athlete_states", "source_workout_log_id")
    op.drop_constraint("ck_workout_logs_timestamp_basis", "workout_logs", type_="check")
    op.drop_constraint("ck_workout_logs_record_only_has_reason", "workout_logs", type_="check")
    op.drop_constraint("ck_workout_logs_state_disposition", "workout_logs", type_="check")
    op.drop_column("workout_logs", "received_at")
    op.drop_column("workout_logs", "client_timestamp")
    op.drop_column("workout_logs", "timestamp_basis")
    op.drop_column("workout_logs", "state_disposition_reason")
    op.drop_column("workout_logs", "state_disposition")
