"""State-correction receipts (P3b-2): the lineage a replayed head carries.

A late workout or benchmark that is folded in by exact tail replay produces a *correction head*:
a new state row at the old head's timestamp (it sorts after it by id) holding the replayed
state. The rows between the earliest introduced event and the old head stay stored, now out of
date ("stale"); nothing is deleted. The receipt says which events the correction introduced,
where it started, and which code ran it, so a later correction can include them exactly once.

* ``state_corrections``: one receipt per correction. Append-only (a trigger refuses an UPDATE or
  a DELETE).
* ``state_correction_events``: the events it introduced, in batch order. An event is introduced
  at most once, ever (unique indexes), and the membership set cannot be edited or shrunk (the
  same trigger): deleting a member would also release its uniqueness protection. A receipt is
  never a training event: replay reads training events from the workout and observation tables,
  and introduced events from here.
* ``athlete_states.source_correction_id``: set exactly on ``event_kind = 'correction'`` rows.
* ``event_kind`` gains ``correction``; a correction row, like a workout or benchmark row, must
  carry a transition identity.

Nothing writes these yet (P3b-3 applies corrections). Removing a receipt is a deliberate
operator act (``ALTER TABLE ... DISABLE TRIGGER``); TRUNCATE (test cleanup) is not row-level and
is unaffected.

The downgrade refuses while any correction exists: dropping the receipts would leave corrected
heads that no longer say how they were made.

Revision ID: a056_state_corrections
Revises: a055_replay_capture
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a056_state_corrections"
down_revision: str | None = "a055_replay_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_OLD = "('baseline', 'workout', 'benchmark', 'repair')"
_KINDS_NEW = "('baseline', 'workout', 'benchmark', 'repair', 'correction')"


def upgrade() -> None:
    op.create_table(
        "state_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("algorithm_version", sa.String(), nullable=False),
        sa.Column(
            "transition_identity", sa.String(64),
            sa.ForeignKey("engine_transition_identities.digest"), nullable=False,
        ),
        sa.Column("checkpoint_state_id", sa.Integer(), sa.ForeignKey("athlete_states.id"), nullable=False),
        sa.Column("head_before_state_id", sa.Integer(), sa.ForeignKey("athlete_states.id"), nullable=False),
        sa.Column("affected_from", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "state_correction_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "correction_id", sa.Integer(),
            sa.ForeignKey("state_corrections.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("workout_log_id", sa.Integer(), sa.ForeignKey("workout_logs.id"), nullable=True),
        sa.Column(
            "observation_id", sa.Integer(), sa.ForeignKey("benchmark_observations.id"), nullable=True
        ),
        sa.UniqueConstraint("correction_id", "ordinal", name="uq_state_correction_events_ordinal"),
        sa.CheckConstraint(
            "(workout_log_id IS NULL) <> (observation_id IS NULL)",
            name="ck_state_correction_events_one_event",
        ),
    )
    op.create_index(
        "uq_state_correction_events_workout", "state_correction_events", ["workout_log_id"],
        unique=True, postgresql_where=sa.text("workout_log_id IS NOT NULL"),
    )
    op.create_index(
        "uq_state_correction_events_observation", "state_correction_events", ["observation_id"],
        unique=True, postgresql_where=sa.text("observation_id IS NOT NULL"),
    )

    op.add_column("athlete_states", sa.Column("source_correction_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_athlete_states_source_correction_id",
        "athlete_states", "state_corrections", ["source_correction_id"], ["id"],
    )
    op.create_index(
        "uq_athlete_states_source_correction", "athlete_states", ["source_correction_id"],
        unique=True, postgresql_where=sa.text("source_correction_id IS NOT NULL"),
    )
    op.drop_constraint("ck_athlete_states_event_kind", "athlete_states", type_="check")
    op.create_check_constraint(
        "ck_athlete_states_event_kind", "athlete_states",
        f"event_kind IS NULL OR event_kind IN {_KINDS_NEW}",
    )
    op.drop_constraint("ck_athlete_states_transition_has_identity", "athlete_states", type_="check")
    op.create_check_constraint(
        "ck_athlete_states_transition_has_identity", "athlete_states",
        "event_kind IS NULL OR event_kind NOT IN ('workout', 'benchmark', 'correction') "
        "OR transition_identity IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_athlete_states_correction_link", "athlete_states",
        "(event_kind IS NOT DISTINCT FROM 'correction') = (source_correction_id IS NOT NULL)",
    )

    op.execute(
        """
        CREATE FUNCTION forbid_receipt_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'receipts are append-only (% on % id %)', TG_OP, TG_TABLE_NAME, OLD.id
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("state_corrections", "state_correction_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION forbid_receipt_change()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM state_corrections LIMIT 1")).first() is not None:
        raise RuntimeError(
            "refusing to downgrade a056: state corrections exist. Their receipts say how the "
            "corrected heads were made; dropping them would leave heads with no lineage."
        )
    for table in ("state_corrections", "state_correction_events"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS forbid_receipt_change()")

    op.drop_constraint("ck_athlete_states_correction_link", "athlete_states", type_="check")
    op.drop_constraint("ck_athlete_states_transition_has_identity", "athlete_states", type_="check")
    op.create_check_constraint(
        "ck_athlete_states_transition_has_identity", "athlete_states",
        "event_kind IS NULL OR event_kind NOT IN ('workout', 'benchmark') "
        "OR transition_identity IS NOT NULL",
    )
    op.drop_constraint("ck_athlete_states_event_kind", "athlete_states", type_="check")
    op.create_check_constraint(
        "ck_athlete_states_event_kind", "athlete_states",
        f"event_kind IS NULL OR event_kind IN {_KINDS_OLD}",
    )
    op.drop_index("uq_athlete_states_source_correction", table_name="athlete_states")
    op.drop_constraint("fk_athlete_states_source_correction_id", "athlete_states", type_="foreignkey")
    op.drop_column("athlete_states", "source_correction_id")
    op.drop_index("uq_state_correction_events_observation", table_name="state_correction_events")
    op.drop_index("uq_state_correction_events_workout", table_name="state_correction_events")
    op.drop_table("state_correction_events")
    op.drop_table("state_corrections")
