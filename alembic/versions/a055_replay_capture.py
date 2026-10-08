"""Replay capture (P3b-1): record what a state transition was computed from.

Exact tail replay needs every input the live transition had, and the identity of the code and
parameters that ran it. Nothing here changes behaviour; the writers stamp these columns.

* ``engine_transition_identities``: one row per distinct operator-source + parameters digest,
  with the component digests kept beside it.
* ``athlete_states``:
  - ``event_kind`` (baseline | workout | benchmark | repair), the kind of event that wrote the row.
  - ``predecessor_state_id``, the row the transition started from.
  - ``transition_identity``, the digest of what computed it. A workout or benchmark row must
    carry one (check constraint), so a writer cannot forget it.
  - ``anchored_from``, the timestamp a baseline had before the first event re-anchored it.
* ``workout_logs.replay_input`` and ``benchmark_observations.replay_input``: the operator's
  inputs as it saw them. A trigger refuses a change once written.

Rows written before this release keep NULLs everywhere: they are not replayable, and a replay
treats missing capture as "record only". There is no backfill, because inputs that were never
persisted cannot be reconstructed honestly.

The downgrade drops the capture (the columns, the triggers and the identity table); the data is
lost with it.

Revision ID: a055_replay_capture
Revises: a054_benchmark_state_disposition
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a055_replay_capture"
down_revision: str | None = "a054_benchmark_state_disposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUARDED = ("workout_logs", "benchmark_observations")


def upgrade() -> None:
    op.create_table(
        "engine_transition_identities",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("components", postgresql.JSONB(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.add_column("athlete_states", sa.Column("event_kind", sa.String(), nullable=True))
    op.add_column("athlete_states", sa.Column("predecessor_state_id", sa.Integer(), nullable=True))
    op.add_column("athlete_states", sa.Column("transition_identity", sa.String(64), nullable=True))
    op.add_column("athlete_states", sa.Column("anchored_from", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_athlete_states_predecessor_state_id",
        "athlete_states", "athlete_states", ["predecessor_state_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_athlete_states_transition_identity",
        "athlete_states", "engine_transition_identities", ["transition_identity"], ["digest"],
    )
    op.create_check_constraint(
        "ck_athlete_states_event_kind",
        "athlete_states",
        "event_kind IS NULL OR event_kind IN ('baseline', 'workout', 'benchmark', 'repair')",
    )
    op.create_check_constraint(
        "ck_athlete_states_transition_has_identity",
        "athlete_states",
        "event_kind IS NULL OR event_kind NOT IN ('workout', 'benchmark') "
        "OR transition_identity IS NOT NULL",
    )

    for table in _GUARDED:
        op.add_column(table, sa.Column("replay_input", postgresql.JSONB(), nullable=True))

    op.execute(
        """
        CREATE FUNCTION prevent_replay_input_change() RETURNS trigger AS $$
        BEGIN
            IF OLD.replay_input IS NOT NULL AND NEW.replay_input IS DISTINCT FROM OLD.replay_input THEN
                RAISE EXCEPTION 'replay_input is immutable once written (% id %)', TG_TABLE_NAME, OLD.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _GUARDED:
        op.execute(
            f"CREATE TRIGGER trg_{table}_replay_input_immutable "
            f"BEFORE UPDATE OF replay_input ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION prevent_replay_input_change()"
        )


def downgrade() -> None:
    for table in _GUARDED:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_replay_input_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_replay_input_change()")
    for table in _GUARDED:
        op.drop_column(table, "replay_input")

    op.drop_constraint("ck_athlete_states_transition_has_identity", "athlete_states", type_="check")
    op.drop_constraint("ck_athlete_states_event_kind", "athlete_states", type_="check")
    op.drop_constraint("fk_athlete_states_transition_identity", "athlete_states", type_="foreignkey")
    op.drop_constraint("fk_athlete_states_predecessor_state_id", "athlete_states", type_="foreignkey")
    op.drop_column("athlete_states", "anchored_from")
    op.drop_column("athlete_states", "transition_identity")
    op.drop_column("athlete_states", "predecessor_state_id")
    op.drop_column("athlete_states", "event_kind")
    op.drop_table("engine_transition_identities")
