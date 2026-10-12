"""Underived strength reports (P4-3a): a reported set the server records but derives nothing from.

A ``rep_set`` whose effort is unknown (no RPE and no RIR) or conflicting (RPE and RIR further
apart than the resolver's tolerance) cannot be characterized, so it yields no e1RM, no
prescription permission, no capacity effect and no decline input. It is kept here, as the
athlete's facts, instead of as a ``benchmark_observations`` row: that table's ``raw_value`` is
NOT NULL, and neither 0 nor the lifted load may stand in for a value that does not exist.

* ``underived_strength_reports``: lift, load, reps, the reported effort, when it was performed
  (NULL stays unknown), the collection mode, and why nothing was derived. The reason is pinned
  to the reported effort: ``effort_unknown`` carries neither RPE nor RIR, ``effort_conflict``
  carries both. Whether a pair conflicts is the shared resolver's call, not the database's: no
  tolerance is repeated here. Load, reps, RPE and RIR keep the request schema's bounds (finite
  positive load, reps >= 1, RPE 1-10, RIR 0-10). Only ``rep_set`` reports are classified this way.
* Lookup by athlete, lift and performed time: ``ix_underived_strength_reports_lookup``.
* Supersession: a later characterized submission that names this report becomes the
  ``benchmark_observations`` row recorded in ``superseded_by_observation_id``. A report is
  superseded at most once, and an observation supersedes at most one report.
* A trigger keeps the facts permanent: the supersession columns may be set once (from NULL);
  every other column is immutable, a superseded report cannot change again, and a report cannot
  be deleted. TRUNCATE (test cleanup) is not row-level and is unaffected.

Nothing writes or reads this table yet (P4-3b does). The downgrade refuses while any report
exists: dropping them would erase what athletes reported.

Revision ID: a058_underived_strength_reports
Revises: a057_replay_refusal
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a058_underived_strength_reports"
down_revision: str | None = "a057_replay_refusal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "underived_strength_reports"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "benchmark_definition_id", sa.Integer(),
            sa.ForeignKey("benchmark_definitions.id"), nullable=False,
        ),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("collection_mode", sa.String(40), nullable=False),
        sa.Column("performed_at", sa.DateTime(), nullable=True),
        sa.Column("load_kg", sa.Float(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("rir", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "superseded_by_observation_id", sa.Integer(),
            sa.ForeignKey("benchmark_observations.id"), nullable=True,
        ),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("method = 'rep_set'", name="ck_underived_strength_reports_method"),
        sa.CheckConstraint(
            "collection_mode IN ('onboarding_onramp', 'retest')",
            name="ck_underived_strength_reports_collection_mode",
        ),
        # The request schema's bounds. NaN and infinity compare above every finite float in
        # PostgreSQL, so the upper bounds also refuse them.
        sa.CheckConstraint(
            "load_kg > 0 AND load_kg < 'Infinity' AND reps >= 1",
            name="ck_underived_strength_reports_set",
        ),
        sa.CheckConstraint(
            "rpe IS NULL OR (rpe >= 1 AND rpe <= 10)", name="ck_underived_strength_reports_rpe"
        ),
        sa.CheckConstraint(
            "rir IS NULL OR (rir >= 0 AND rir <= 10)", name="ck_underived_strength_reports_rir"
        ),
        sa.CheckConstraint(
            "(reason = 'effort_unknown' AND rpe IS NULL AND rir IS NULL) "
            "OR (reason = 'effort_conflict' AND rpe IS NOT NULL AND rir IS NOT NULL)",
            name="ck_underived_strength_reports_reason",
        ),
        sa.CheckConstraint(
            "(superseded_by_observation_id IS NULL) = (superseded_at IS NULL)",
            name="ck_underived_strength_reports_superseded",
        ),
    )
    op.create_index(
        "ix_underived_strength_reports_lookup", _TABLE,
        ["user_id", "benchmark_definition_id", "performed_at"],
    )
    op.create_index(
        "uq_underived_strength_reports_superseded_by", _TABLE, ["superseded_by_observation_id"],
        unique=True, postgresql_where=sa.text("superseded_by_observation_id IS NOT NULL"),
    )

    # Compare whole rows minus the supersession columns, so a column added later is immutable
    # by default rather than by remembering to list it here.
    op.execute(
        """
        CREATE FUNCTION guard_underived_strength_report() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'underived strength reports are permanent (DELETE on id %)', OLD.id
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.superseded_by_observation_id IS NOT NULL THEN
                RAISE EXCEPTION 'underived strength report % is already superseded', OLD.id
                    USING ERRCODE = '23514';
            END IF;
            IF (to_jsonb(NEW) - 'superseded_by_observation_id' - 'superseded_at')
                IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_by_observation_id' - 'superseded_at')
            THEN
                RAISE EXCEPTION 'underived strength report facts are immutable (id %)', OLD.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_{_TABLE}_guard BEFORE UPDATE OR DELETE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION guard_underived_strength_report()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(
            "refusing to downgrade a058: underived strength reports exist. They are what "
            "athletes reported; dropping the table would erase them."
        )
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_guard ON {_TABLE}")
    op.execute("DROP FUNCTION IF EXISTS guard_underived_strength_report()")
    op.drop_index("uq_underived_strength_reports_superseded_by", table_name=_TABLE)
    op.drop_index("ix_underived_strength_reports_lookup", table_name=_TABLE)
    op.drop_table(_TABLE)
