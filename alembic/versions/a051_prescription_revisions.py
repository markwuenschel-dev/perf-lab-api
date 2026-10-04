"""Immutable prescription revisions (P1).

``planned_sessions.prescribed_content`` was overwritten in place on every GET, so the workout an
athlete was shown — and logged against — could be silently replaced. Each issued prescription is
now an immutable ``prescription_revisions`` row; ``planned_sessions.current_revision_id`` points at
the live one and ``prescribed_content`` stays as its mirror for existing readers.

* ``revision_no`` increases per session for the session's whole life (never reset).
* Content is immutable: a trigger rejects any UPDATE of the identity/content columns.
* Existing ``prescribed_content`` is preserved as revision 0 with ``origin='legacy_unknown'``:
  whether the athlete ever saw it is unknown, so it is NOT made current — the first read after
  deploy issues revision 1.
* ``workout_logs.prescription_revision_id`` records exactly which revision a log was linked to;
  ``prescription_decisions.prescription_revision_id`` ties decision telemetry to what it issued.

Revision ID: a051_prescription_revisions
Revises: a050_decision_final_outcome
Create Date: 2026-10-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a051_prescription_revisions"
down_revision: str | None = "a050_decision_final_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_FN = "prescription_revisions_immutable"


def upgrade() -> None:
    op.create_table(
        "prescription_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "planned_session_id",
            sa.Integer(),
            sa.ForeignKey("planned_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("safety_kind", sa.String(length=32), nullable=True),
        sa.Column("safety_signature", postgresql.JSONB(), nullable=True),
        sa.Column("versions", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("evaluation_date", sa.Date(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("planned_session_id", "revision_no", name="uq_prescription_revision_no"),
    )
    op.create_index(
        "ix_prescription_revisions_planned_session_id",
        "prescription_revisions",
        ["planned_session_id"],
    )
    op.add_column(
        "planned_sessions",
        sa.Column(
            "current_revision_id",
            sa.Integer(),
            sa.ForeignKey(
                "prescription_revisions.id",
                ondelete="SET NULL",
                name="fk_planned_sessions_current_revision_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "workout_logs",
        sa.Column(
            "prescription_revision_id",
            sa.Integer(),
            sa.ForeignKey(
                "prescription_revisions.id",
                ondelete="SET NULL",
                name="fk_workout_logs_prescription_revision_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "prescription_decisions",
        sa.Column(
            "prescription_revision_id",
            sa.Integer(),
            sa.ForeignKey(
                "prescription_revisions.id",
                ondelete="SET NULL",
                name="fk_prescription_decisions_prescription_revision_id",
            ),
            nullable=True,
        ),
    )

    # Immutability: what a revision prescribed never changes after it is written.
    op.execute(
        f"""
        CREATE FUNCTION {_IMMUTABLE_FN}() RETURNS trigger AS $$
        BEGIN
            IF NEW.content IS DISTINCT FROM OLD.content
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.revision_no IS DISTINCT FROM OLD.revision_no
               OR NEW.planned_session_id IS DISTINCT FROM OLD.planned_session_id
               OR NEW.safety_signature IS DISTINCT FROM OLD.safety_signature
               OR NEW.reason IS DISTINCT FROM OLD.reason
               OR NEW.origin IS DISTINCT FROM OLD.origin THEN
                RAISE EXCEPTION 'prescription revisions are immutable (revision %)', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER {_IMMUTABLE_FN}_trg BEFORE UPDATE ON prescription_revisions "
        f"FOR EACH ROW EXECUTE FUNCTION {_IMMUTABLE_FN}()"
    )

    # Preserve what is already stored — as legacy, never as the current revision.
    op.execute(
        """
        INSERT INTO prescription_revisions
            (planned_session_id, user_id, revision_no, content, content_hash,
             reason, origin, evaluation_date)
        SELECT id, user_id, 0, prescribed_content, md5(prescribed_content::text),
               'legacy_preserved', 'legacy_unknown', scheduled_date
        FROM planned_sessions
        WHERE prescribed_content IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("prescription_decisions", "prescription_revision_id")
    op.drop_column("workout_logs", "prescription_revision_id")
    op.drop_column("planned_sessions", "current_revision_id")
    op.execute(f"DROP TRIGGER IF EXISTS {_IMMUTABLE_FN}_trg ON prescription_revisions")
    op.execute(f"DROP FUNCTION IF EXISTS {_IMMUTABLE_FN}()")
    op.drop_index("ix_prescription_revisions_planned_session_id", table_name="prescription_revisions")
    op.drop_table("prescription_revisions")
