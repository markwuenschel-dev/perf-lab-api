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
value, and restores the unique constraint. Before touching the schema it refuses while any
feedback is superseded (dropping the marker would reactivate it) or any active feedback would
stop describing its session (feedback about a miss, once the miss becomes ``pending``).

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
    # Every precondition is checked BEFORE any schema change. Pre-P2 code has no notion of
    # supersession: dropping ``superseded_at`` would make every superseded row active again,
    # and pre-P2 code reads any feedback row as the session's current outcome. So refuse:
    #   * any superseded row at all — even a single one would be reactivated;
    #   * any active row that would no longer describe its session once ``missed`` becomes
    #     ``pending`` below (feedback about a miss would sit on a pending session).
    #
    # The checks are only true if nothing writes between them and the schema change, and the
    # API keeps serving during a rollback. EXCLUSIVE blocks every writer (including SELECT …
    # FOR UPDATE, which all session/feedback writers start with) but not plain reads, and is
    # held until this migration's transaction commits (Postgres DDL is transactional). A
    # writer that got there first is waited out, so its changes are what the checks see.
    # Order matches the writers' own: the session row, then its feedback.
    op.execute("LOCK TABLE planned_sessions, session_feedback IN EXCLUSIVE MODE")
    bind = op.get_bind()
    superseded = bind.execute(
        sa.text("SELECT count(*) FROM session_feedback WHERE superseded_at IS NOT NULL")
    ).scalar_one()
    stranded = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM session_feedback sf
              JOIN planned_sessions ps ON ps.id = sf.planned_session_id
             WHERE sf.superseded_at IS NULL
               AND sf.describes_status IS DISTINCT FROM
                   CASE ps.status::text WHEN 'missed' THEN 'pending' ELSE ps.status::text END
            """
        )
    ).scalar_one()
    if superseded or stranded:
        raise RuntimeError(
            f"Refusing to downgrade a052: {superseded} superseded feedback row(s) would become "
            f"active again, and {stranded} active row(s) would describe an outcome their session "
            "no longer has. Export and resolve them first (docs/DEPLOY.md, missed sessions)."
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
