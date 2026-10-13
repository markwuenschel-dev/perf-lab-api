"""OAuth authorization attempts and connection generations, schema only (P6a, ADR-0075).

P6b replaces the stateless JWT ``state`` with a durable attempt record. This revision adds the
schema for it and nothing that reads or writes it, so the P6a writers (legacy callback, PAT
connect, token refresh, disconnect) run unchanged on top of it, and P6b can roll back to P6a by
SHA with no database step.

* ``oauth_authorizations``: one row per authorization attempt. Its inputs (owner, provider,
  state hash, PKCE challenge, redirect URI, creation and expiry time) never change. Its status
  moves only ``pending → claimed → succeeded | failed`` or ``pending → failed``, and a terminal
  row never changes again except that its live ``connection_id`` becomes NULL when the
  connection is deleted. A CHECK pins the columns each status may and must carry; a trigger
  enforces the transitions and immutability, which a CHECK cannot see.
* ``receipt_connection_id`` / ``receipt_generation``: the connection a successful attempt
  linked, as it was at that moment. ``receipt_connection_id`` has no FK so a disconnect cannot
  change a receipt; ``connection_id`` is the live reference and is set NULL on delete.
* ``wearable_connections.generation``: counts successful links of that row, from 1. The DB
  default lets today's writer keep inserting without it; existing rows get 1.

The downgrade refuses, changing nothing, while any attempt row exists or any connection is past
generation 1: dropping either would discard history. Delete deliberately first if that is the
intent (docs/DEPLOY.md, P6).

Revision ID: a061_oauth_authorizations
Revises: a060_session_block_day
Create Date: 2026-10-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a061_oauth_authorizations"
down_revision: str | None = "a060_session_block_day"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "oauth_authorizations"
_CONNECTIONS = "wearable_connections"
_GUARD_FN = "oauth_authorizations_guard"
_GUARD_TRIGGER = "trg_oauth_authorizations_guard"
_GENERATION_CHECK = "ck_wearable_connections_generation"

# Each status carries exactly its own columns. ``receipt_generation IS NOT NULL`` is spelled
# out: ``>= 1`` alone is NULL, not false, for a NULL generation, and CHECK passes on NULL.
STATUS_SHAPE = (
    "(status = 'pending' AND claimed_at IS NULL AND outcome_at IS NULL AND error IS NULL"
    " AND receipt_connection_id IS NULL AND receipt_generation IS NULL AND connection_id IS NULL)"
    " OR (status = 'claimed' AND claimed_at IS NOT NULL AND outcome_at IS NULL AND error IS NULL"
    " AND receipt_connection_id IS NULL AND receipt_generation IS NULL AND connection_id IS NULL)"
    " OR (status = 'succeeded' AND claimed_at IS NOT NULL AND outcome_at IS NOT NULL"
    " AND error IS NULL AND receipt_connection_id IS NOT NULL AND receipt_generation IS NOT NULL"
    " AND receipt_generation >= 1"
    " AND (connection_id IS NULL OR connection_id = receipt_connection_id))"
    " OR (status = 'failed' AND outcome_at IS NOT NULL AND error IS NOT NULL"
    " AND receipt_connection_id IS NULL AND receipt_generation IS NULL AND connection_id IS NULL)"
)

_GUARD_SQL = f"""
CREATE FUNCTION {_GUARD_FN}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.state_hash IS DISTINCT FROM OLD.state_hash
       OR NEW.code_challenge IS DISTINCT FROM OLD.code_challenge
       OR NEW.redirect_uri IS DISTINCT FROM OLD.redirect_uri
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'oauth_authorizations %: attempt inputs are immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.status IN ('succeeded', 'failed') THEN
        -- A terminal outcome is final. The one permitted change is the live reference going
        -- NULL, which is what ON DELETE SET NULL does when the connection is deleted.
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
           OR NEW.outcome_at IS DISTINCT FROM OLD.outcome_at
           OR NEW.error IS DISTINCT FROM OLD.error
           OR NEW.receipt_connection_id IS DISTINCT FROM OLD.receipt_connection_id
           OR NEW.receipt_generation IS DISTINCT FROM OLD.receipt_generation
           OR (NEW.connection_id IS DISTINCT FROM OLD.connection_id
               AND NEW.connection_id IS NOT NULL) THEN
            RAISE EXCEPTION 'oauth_authorizations %: a % attempt is final', OLD.id, OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'pending' AND NEW.status IN ('claimed', 'failed'))
        OR (OLD.status = 'claimed' AND NEW.status IN ('succeeded', 'failed'))) THEN
        RAISE EXCEPTION 'oauth_authorizations %: % -> % is not a transition', OLD.id,
            OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.claimed_at IS NOT NULL AND NEW.claimed_at IS DISTINCT FROM OLD.claimed_at THEN
        RAISE EXCEPTION 'oauth_authorizations %: claimed_at is set once', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.add_column(
        _CONNECTIONS,
        sa.Column(
            "generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Successful links of this row, from 1; refresh does not change it",
        ),
    )
    op.create_check_constraint(_GENERATION_CHECK, _CONNECTIONS, "generation >= 1")

    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("outcome_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(40), nullable=True),
        sa.Column("receipt_connection_id", sa.Integer(), nullable=True),
        sa.Column("receipt_generation", sa.Integer(), nullable=True),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey(f"{_CONNECTIONS}.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("state_hash", name="uq_oauth_authorizations_state_hash"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'succeeded', 'failed')",
            name="ck_oauth_authorizations_status",
        ),
        sa.CheckConstraint(STATUS_SHAPE, name="ck_oauth_authorizations_status_shape"),
        sa.CheckConstraint("expires_at > created_at", name="ck_oauth_authorizations_expiry"),
        sa.CheckConstraint(
            "(claimed_at IS NULL OR claimed_at >= created_at)"
            " AND (outcome_at IS NULL OR outcome_at >= created_at)"
            " AND (outcome_at IS NULL OR claimed_at IS NULL OR outcome_at >= claimed_at)",
            name="ck_oauth_authorizations_time_order",
        ),
        sa.CheckConstraint(
            "state_hash ~ '^[0-9a-f]{64}$'", name="ck_oauth_authorizations_state_hash"
        ),
        sa.CheckConstraint(
            "code_challenge ~ '^[A-Za-z0-9_-]{43}$'",
            name="ck_oauth_authorizations_code_challenge",
        ),
        sa.CheckConstraint(
            "error IS NULL OR error ~ '^[a-z][a-z_]{0,39}$'", name="ck_oauth_authorizations_error"
        ),
        sa.CheckConstraint("provider <> ''", name="ck_oauth_authorizations_provider"),
    )
    op.create_index("ix_oauth_authorizations_user_created", _TABLE, ["user_id", "created_at"])
    # ON DELETE SET NULL scans for referencing rows; keep that an index lookup.
    op.create_index(
        "ix_oauth_authorizations_connection",
        _TABLE,
        ["connection_id"],
        postgresql_where=sa.text("connection_id IS NOT NULL"),
    )

    op.execute(_GUARD_SQL)
    op.execute(
        f"CREATE TRIGGER {_GUARD_TRIGGER} BEFORE UPDATE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_GUARD_FN}()"
    )


def downgrade() -> None:
    # Both counts and the drops must see the same rows: block writers until this commits.
    op.execute(f"LOCK TABLE {_TABLE}, {_CONNECTIONS} IN EXCLUSIVE MODE")
    bind = op.get_bind()
    attempts = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    relinked = bind.execute(
        sa.text(f"SELECT count(*) FROM {_CONNECTIONS} WHERE generation > 1")
    ).scalar_one()
    if attempts or relinked:
        raise RuntimeError(
            f"Refusing to downgrade a061: {attempts} OAuth authorization attempt(s) and "
            f"{relinked} connection(s) past generation 1 would be discarded. Nothing was "
            "changed. Rolling P6b back needs no downgrade (deploy the P6a SHA); to discard this "
            f"history deliberately, DELETE FROM {_TABLE} and reset generation first "
            "(docs/DEPLOY.md, P6)."
        )

    op.execute(f"DROP TRIGGER {_GUARD_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION {_GUARD_FN}()")
    op.drop_index("ix_oauth_authorizations_connection", table_name=_TABLE)
    op.drop_index("ix_oauth_authorizations_user_created", table_name=_TABLE)
    op.drop_table(_TABLE)
    op.drop_constraint(_GENERATION_CHECK, _CONNECTIONS, type_="check")
    op.drop_column(_CONNECTIONS, "generation")
