"""Planned sessions keep their block-relative slot; ``day_of_week`` becomes a real weekday (P5a).

The generator places a session on ``start_date + (week - 1) * 7 + (slot - 1)``: a template
slot is "day N of the block week", counted from the block's start date, not a weekday. Until
now the session stored that slot as ``day_of_week``, whose comment said ``1=Mon, 7=Sun``, which
held only for blocks that start on a Monday.

* ``block_day``: the slot the session was generated from (1-7, block-relative). Copied from the
  stored ``day_of_week`` before anything is relabeled, so it is the recorded slot, not one
  inferred from dates. Set at creation and never changed, including by a reschedule.
* ``day_of_week``: the ISO weekday (1=Mon, 7=Sun) of the session's plan date,
  ``COALESCE(original_scheduled_date, scheduled_date)``. A reschedule does not change it; the
  current calendar weekday comes from ``scheduled_date``.

Limitation: sessions moved before ``original_scheduled_date`` existed (a003, 2026-06-20) lost
their plan date, so their ``day_of_week`` is the weekday of the date they were moved to. Their
``block_day`` is still the recorded slot. Dates, status, week numbers and prescriptions are not
touched.

Both columns are checked to 1-7. An existing ``day_of_week`` outside 1-7 refuses the upgrade
(nothing is changed): no slot is invented for it.

The downgrade copies ``block_day`` back into ``day_of_week`` and drops it, which restores the
slot every session was generated from, including sessions created after this upgrade. Stop the
API before downgrading (docs/DEPLOY.md, P5a): a running P5a process would keep writing weekdays.

Revision ID: a060_session_block_day
Revises: a059_strength_submission_keys
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a060_session_block_day"
down_revision: str | None = "a059_strength_submission_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "planned_sessions"
_BLOCK_DAY_CHECK = "ck_planned_sessions_block_day"
_DAY_OF_WEEK_CHECK = "ck_planned_sessions_day_of_week"


def upgrade() -> None:
    # The copy and the relabel must see the same rows: EXCLUSIVE blocks every writer (not plain
    # reads) until this transaction commits.
    op.execute(f"LOCK TABLE {_TABLE} IN EXCLUSIVE MODE")
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(f"SELECT count(*) FROM {_TABLE} WHERE day_of_week NOT BETWEEN 1 AND 7")
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            f"Refusing to upgrade to a060: {invalid} planned session(s) have a day_of_week outside "
            "1-7, so their block-relative slot is unknown. Nothing was changed. Find them with "
            f"SELECT id, block_id, day_of_week FROM {_TABLE} WHERE day_of_week NOT BETWEEN 1 AND 7 "
            "and correct them first (docs/DEPLOY.md, P5a)."
        )

    op.add_column(_TABLE, sa.Column("block_day", sa.SmallInteger(), nullable=True))
    op.execute(f"UPDATE {_TABLE} SET block_day = day_of_week")
    op.alter_column(
        _TABLE,
        "block_day",
        nullable=False,
        comment="Block-relative template slot 1-7 (day N of the block week); never changes",
    )
    op.create_check_constraint(_BLOCK_DAY_CHECK, _TABLE, "block_day BETWEEN 1 AND 7")

    op.execute(
        f"UPDATE {_TABLE} SET day_of_week = "
        "EXTRACT(ISODOW FROM COALESCE(original_scheduled_date, scheduled_date))::int"
    )
    op.alter_column(
        _TABLE,
        "day_of_week",
        comment="ISO weekday (1=Mon, 7=Sun) of COALESCE(original_scheduled_date, scheduled_date)",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.create_check_constraint(_DAY_OF_WEEK_CHECK, _TABLE, "day_of_week BETWEEN 1 AND 7")


def downgrade() -> None:
    op.execute(f"LOCK TABLE {_TABLE} IN EXCLUSIVE MODE")
    op.drop_constraint(_DAY_OF_WEEK_CHECK, _TABLE, type_="check")
    op.execute(f"UPDATE {_TABLE} SET day_of_week = block_day")
    # a000 created the column without a comment.
    op.alter_column(
        _TABLE,
        "day_of_week",
        comment=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_constraint(_BLOCK_DAY_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "block_day")
