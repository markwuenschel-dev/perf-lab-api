"""Strength submission keys (P4-3a): a retried strength submission returns its first outcome.

A client sends an optional ``idempotency_key`` (a UUID) with a standalone strength submission.
The key names one logical submission, not one button press: a retry after an uncertain failure
resends the same key with the same payload; an edited payload, or another identical set recorded
on purpose, gets a new key.

* ``strength_submission_keys``: one row per keyed submission that recorded something. Keys are a
  per-athlete namespace (unique on user and key); that is not ownership of the effect. The schema
  does not check that the observation or report a key names belongs to the same athlete, so the
  writer (P4-3b) must refuse a foreign-owned effect before storing an outcome. It holds the canonical submitted payload and its digest
  (what a reused key is compared against), the one effect it produced (an observation or an
  underived report, never both, and an effect belongs to at most one key), and the outcome as it
  was first returned, so a replay answers from the row without repeating any effect.
* The row is written in the same transaction as the effect it names, so a key never exists
  without its effect. A refused or failed submission writes no key.
* Append-only: a trigger refuses an UPDATE or a DELETE. TRUNCATE (test cleanup) is not row-level
  and is unaffected.

The guarantee starts with keyed submissions: a request sent without a key, including every
request before this release, cannot be recognized as a retry.

Nothing writes or reads this table yet (P4-3b does). The downgrade refuses while any key exists.

Revision ID: a059_strength_submission_keys
Revises: a058_underived_strength_reports
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a059_strength_submission_keys"
down_revision: str | None = "a058_underived_strength_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "strength_submission_keys"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "observation_id", sa.Integer(), sa.ForeignKey("benchmark_observations.id"), nullable=True
        ),
        sa.Column(
            "report_id", sa.Integer(), sa.ForeignKey("underived_strength_reports.id"), nullable=True
        ),
        sa.Column("outcome", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_strength_submission_keys_key"),
        sa.CheckConstraint(
            "(observation_id IS NULL) <> (report_id IS NULL)",
            name="ck_strength_submission_keys_one_effect",
        ),
    )
    op.create_index(
        "uq_strength_submission_keys_observation", _TABLE, ["observation_id"],
        unique=True, postgresql_where=sa.text("observation_id IS NOT NULL"),
    )
    op.create_index(
        "uq_strength_submission_keys_report", _TABLE, ["report_id"],
        unique=True, postgresql_where=sa.text("report_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION forbid_submission_key_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'strength submission keys are append-only (% on id %)', TG_OP, OLD.id
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_{_TABLE}_append_only BEFORE UPDATE OR DELETE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION forbid_submission_key_change()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(
            "refusing to downgrade a059: strength submission keys exist. Dropping them would "
            "let a retry of an acknowledged submission record it a second time."
        )
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_append_only ON {_TABLE}")
    op.execute("DROP FUNCTION IF EXISTS forbid_submission_key_change()")
    op.drop_index("uq_strength_submission_keys_report", table_name=_TABLE)
    op.drop_index("uq_strength_submission_keys_observation", table_name=_TABLE)
    op.drop_table(_TABLE)
