"""Missed sessions and feedback supersession (P2).

A past session nobody logged stayed ``pending`` forever. ``missed`` is the system's inference
that training was not recorded; it is distinct from ``skipped``, which only the athlete
declares. The reconciler writes it (``app.services.missed_session_service``), behind
``RECONCILE_MISSED_SESSIONS``; this migration only makes the value and the feedback shape exist.

Feedback describes the outcome a session had when it was given. When the outcome later changes
(a late log, a reschedule, a reconciliation), the feedback is kept for audit but marked
superseded, and new feedback may follow:

* ``session_feedback.describes_status`` — the session status the feedback described.
* ``session_feedback.superseded_at`` — set when the session left that status.
* one-per-session becomes one ACTIVE per session: the unique constraint is replaced by a
  partial unique index ``WHERE superseded_at IS NULL``.

Backfill: ``describes_status`` comes from what the feedback says (``completed``/``modified`` →
completed, ``skipped`` → skipped), else from the session's status. Feedback whose described
status is not the session's current status (possible only for rows written before F3 locked
the transitions) is superseded at migration time.

Downgrade turns every ``missed`` session back into ``pending``, rebuilds the enum without the
value, and restores the unique constraint — it refuses to run while a session has more than one
feedback row, since one of them would have to be deleted.

Revision ID: a052_missed_sessions
Revises: a051_prescription_revisions
Create Date: 2026-10-05
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a052_missed_sessions"
down_revision: str | None = "a051_prescription_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_INDEX = "uq_session_feedback_active_per_session"


def upgrade() -> None:
    # Postgres ≥ 12 allows this inside a transaction; the value is not used in this one.
    op.execute("ALTER TYPE sessionstatus ADD VALUE IF NOT EXISTS 'missed'")

    op.add_column("session_feedback", sa.Column("describes_status", sa.String(), nullable=True))
    op.add_column("session_feedback", sa.Column("superseded_at", sa.DateTime(), nullable=True))
    op.execute(
        """
        UPDATE session_feedback sf
           SET describes_status = CASE
                 WHEN sf.status IN ('completed', 'modified') THEN 'completed'
                 WHEN sf.status = 'skipped' THEN 'skipped'
                 ELSE ps.status::text
               END
          FROM planned_sessions ps
         WHERE ps.id = sf.planned_session_id
        """
    )
    op.execute(
        """
        UPDATE session_feedback sf
           SET superseded_at = now() AT TIME ZONE 'utc'
          FROM planned_sessions ps
         WHERE ps.id = sf.planned_session_id
           AND sf.describes_status IS DISTINCT FROM ps.status::text
        """
    )
    op.drop_constraint(
        "session_feedback_planned_session_id_key", "session_feedback", type_="unique"
    )
    op.create_index(
        _ACTIVE_INDEX,
        "session_feedback",
        ["planned_session_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicated = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT planned_session_id FROM session_feedback "
            "GROUP BY planned_session_id HAVING count(*) > 1) d"
        )
    ).scalar_one()
    if duplicated:
        raise RuntimeError(
            f"{duplicated} planned session(s) have more than one feedback row (superseded "
            "history); restoring one-per-session would delete feedback. Resolve them first."
        )

    op.drop_index(_ACTIVE_INDEX, table_name="session_feedback")
    op.create_unique_constraint(
        "session_feedback_planned_session_id_key", "session_feedback", ["planned_session_id"]
    )
    op.drop_column("session_feedback", "superseded_at")
    op.drop_column("session_feedback", "describes_status")

    # A value cannot be dropped from a Postgres enum: rebuild the type without it.
    op.execute("UPDATE planned_sessions SET status = 'pending' WHERE status = 'missed'")
    op.execute("ALTER TYPE sessionstatus RENAME TO sessionstatus_old")
    op.execute("CREATE TYPE sessionstatus AS ENUM ('pending', 'completed', 'skipped', 'rescheduled')")
    op.execute(
        "ALTER TABLE planned_sessions ALTER COLUMN status TYPE sessionstatus "
        "USING status::text::sessionstatus"
    )
    op.execute("DROP TYPE sessionstatus_old")
