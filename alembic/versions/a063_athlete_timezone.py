"""The athlete's IANA timezone, schema only (B1a).

B1b resolves "today" in the athlete's own calendar instead of the server's. This revision adds
the columns for that and nothing that reads or writes them, so the running image keeps using
the server date and B1b can roll back to B1a by SHA with no database step.

On ``athlete_profiles``:

* ``timezone``: an IANA zone name (``America/Los_Angeles``, ``UTC``). NULL means *unknown*: the
  app falls back to UTC and keeps the missed-session grace day. ``'UTC'`` means the athlete (or
  their browser) said UTC, which is a known zone. The database cannot check a name against
  zoneinfo; the API validates it before it is written.
* ``timezone_source``: how the zone was set, ``browser`` (captured once from the browser while
  the zone was unknown) or ``athlete`` (chosen in Settings). Set exactly when ``timezone`` is.

There is no numeric UTC offset column: an offset cannot carry DST rules, and nothing reads one.

The downgrade refuses, changing nothing, while any athlete has a zone: dropping the column would
throw away a choice the athlete made. Clear the columns deliberately first if that is the intent
(docs/DEPLOY.md, "Athlete timezone (B1)").

Revision ID: a063_athlete_timezone
Revises: a062_shadow_log_provenance
Create Date: 2026-10-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a063_athlete_timezone"
down_revision: str | None = "a062_shadow_log_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "athlete_profiles"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("timezone_source", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_athlete_profiles_timezone_shape", _TABLE,
        "(timezone IS NULL AND timezone_source IS NULL) OR (timezone IS NOT NULL "
        "AND length(timezone) > 0 AND timezone_source IS NOT NULL "
        "AND timezone_source IN ('browser', 'athlete'))",
    )


def downgrade() -> None:
    # The count and the drop must see the same rows: block writers until this commits.
    op.execute(f"LOCK TABLE {_TABLE} IN EXCLUSIVE MODE")
    known = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE timezone IS NOT NULL"
    )).scalar_one()
    if known:
        raise RuntimeError(
            f"Refusing to downgrade a063: {known} athlete(s) have a timezone, which the "
            "downgrade would discard. Nothing was changed. Rolling B1b back needs no downgrade "
            "(deploy the B1a SHA); to discard the zones deliberately, set timezone and "
            "timezone_source to NULL first (docs/DEPLOY.md, Athlete timezone (B1))."
        )
    op.drop_constraint("ck_athlete_profiles_timezone_shape", _TABLE, type_="check")
    op.drop_column(_TABLE, "timezone_source")
    op.drop_column(_TABLE, "timezone")
