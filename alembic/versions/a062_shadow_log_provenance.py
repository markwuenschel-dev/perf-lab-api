"""Wellness provenance and correction chains on the recovery and personalization shadow logs,
schema only (P7a).

P7b runs both shadows after every wellness write (manual, Oura, phone ingest), not only after a
manual POST. A re-sync of an unchanged day must add nothing, and a corrected day must append one
row that supersedes the old one. This revision adds the columns and keys for that and nothing
that writes them, so today's writers keep inserting unlinked rows (all three columns NULL) and
P7b can roll back to P7a by SHA with no database step.

On each table:

* ``source_wellness_sample_id``: the wellness sample the row was computed from. A correction
  re-sync updates that sample in place (``uq_wellness_user_date_source``), so its id is the
  stable identity of "this day from this source". It cascades with the sample, as
  ``ekf_shadow_log`` does. The link is ``(source_wellness_sample_id, user_id)`` against the
  sample's ``(id, user_id)``, so a row can only name its own athlete's sample; that needs a
  ``UNIQUE (id, user_id)`` on ``wellness_samples``, which the primary key already implies.
* ``content_hash``: the hash of the hook's inputs from that sample. Required on a linked row,
  absent on an unlinked one.
* ``supersedes_id``: the row this one corrects, in the same chain.

A chain is the rows for one sample and one model (and, for personalization, one parameter). Its
shape is enforced by keys, not by a writer's care:

* one root per chain: a partial unique index over the chain key where ``supersedes_id IS NULL``;
* each row superseded at most once: ``supersedes_id`` is unique, so the chain is a line and its
  head (the row nothing supersedes) is unique;
* a link never leaves its chain: a composite FK ``(supersedes_id, chain key)`` references
  ``(id, chain key)``. The chain key starts with the sample, so a predecessor has the same
  sample and therefore the same owner;
* no cycle: a row may only supersede an older row (``supersedes_id < id``). Every link points
  strictly backwards, so neither a self-link nor a loop of any length can exist, whether it is
  written by an INSERT or an UPDATE.

Exactly-once is judged against the chain's head, not against every hash the chain has held, so
a value corrected and then corrected back appends a row instead of colliding with its first
appearance. Legacy rows stay unlinked and outside every key.

The downgrade refuses, changing nothing, while any linked row exists: dropping the columns would
discard which sample each row came from and which rows were corrected. Delete deliberately
first if that is the intent (docs/DEPLOY.md, P7).

Revision ID: a062_shadow_log_provenance
Revises: a061_oauth_authorizations
Create Date: 2026-10-15
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a062_shadow_log_provenance"
down_revision: str | None = "a061_oauth_authorizations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table → its short name for constraint names, and the columns besides the sample that name a chain.
CHAINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "recovery_shadow_log": ("recovery_shadow", ("model_version",)),
    "personalization_shadow_log": ("personalization_shadow", ("model_version", "parameter")),
}


_SAMPLES = "wellness_samples"
_SAMPLE_OWNER = "uq_wellness_samples_id_user"


def _key(table: str) -> list[str]:
    return ["source_wellness_sample_id", *CHAINS[table][1]]


def upgrade() -> None:
    op.create_unique_constraint(_SAMPLE_OWNER, _SAMPLES, ["id", "user_id"])
    for table, (short, _) in CHAINS.items():
        key = _key(table)
        op.add_column(table, sa.Column("source_wellness_sample_id", sa.Integer, nullable=True))
        # The sample must be this row's athlete's: (sample, user_id) against the sample's own pair.
        op.create_foreign_key(
            f"fk_{short}_source_wellness_sample", table, _SAMPLES,
            ["source_wellness_sample_id", "user_id"], ["id", "user_id"], ondelete="CASCADE",
        )
        op.add_column(table, sa.Column("content_hash", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("supersedes_id", sa.Integer, nullable=True))
        # An unlinked (legacy or P7a-written) row carries none of the three; a linked row its hash.
        op.create_check_constraint(
            f"ck_{short}_provenance_shape", table,
            "(source_wellness_sample_id IS NULL AND content_hash IS NULL AND supersedes_id IS NULL)"
            " OR (source_wellness_sample_id IS NOT NULL AND content_hash IS NOT NULL)",
        )
        op.create_check_constraint(
            f"ck_{short}_supersedes_older", table, "supersedes_id IS NULL OR supersedes_id < id",
        )
        op.create_unique_constraint(f"uq_{short}_chain_member", table, ["id", *key])
        op.create_foreign_key(
            f"fk_{short}_supersedes_same_chain", table, table,
            ["supersedes_id", *key], ["id", *key], ondelete="CASCADE",
        )
        op.create_index(
            f"uq_{short}_chain_root", table, key, unique=True,
            postgresql_where=sa.text("source_wellness_sample_id IS NOT NULL AND supersedes_id IS NULL"),
        )
        op.create_index(
            f"uq_{short}_supersedes_once", table, ["supersedes_id"], unique=True,
            postgresql_where=sa.text("supersedes_id IS NOT NULL"),
        )


def downgrade() -> None:
    tables = ", ".join(CHAINS)
    # The counts and the drops must see the same rows: block writers until this commits.
    op.execute(f"LOCK TABLE {tables} IN EXCLUSIVE MODE")
    bind = op.get_bind()
    linked = {
        table: bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE source_wellness_sample_id IS NOT NULL"
        )).scalar_one()
        for table in CHAINS
    }
    if any(linked.values()):
        counts = ", ".join(f"{n} in {t}" for t, n in linked.items())
        raise RuntimeError(
            f"Refusing to downgrade a062: linked shadow rows ({counts}) would lose their sample "
            "and correction links. Nothing was changed. Rolling P7b back needs no downgrade "
            "(deploy the P7a SHA); to discard this history deliberately, delete the linked rows "
            "first (docs/DEPLOY.md, P7)."
        )

    for table, (short, _) in CHAINS.items():
        op.drop_index(f"uq_{short}_supersedes_once", table_name=table)
        op.drop_index(f"uq_{short}_chain_root", table_name=table)
        op.drop_constraint(f"fk_{short}_supersedes_same_chain", table, type_="foreignkey")
        op.drop_constraint(f"uq_{short}_chain_member", table, type_="unique")
        op.drop_constraint(f"ck_{short}_supersedes_older", table, type_="check")
        op.drop_constraint(f"ck_{short}_provenance_shape", table, type_="check")
        op.drop_constraint(f"fk_{short}_source_wellness_sample", table, type_="foreignkey")
        op.drop_column(table, "supersedes_id")
        op.drop_column(table, "content_hash")
        op.drop_column(table, "source_wellness_sample_id")
    op.drop_constraint(_SAMPLE_OWNER, _SAMPLES, type_="unique")
