"""AUD-C12: tighten users/athlete_profiles nullability to match the ORM models.

Nine columns are declared NOT NULL by the SQLAlchemy models (non-Optional ``Mapped[...]``)
but were created ``nullable=True`` in a000. The Python-side ``default=`` masks the gap on
the ORM write path, but the database does not enforce the invariant the app assumes: a NULL
written via any non-ORM path (seed script, raw SQL, bulk insert) persists, and
``GET /v1/profile`` then 500s when ``ProfileRead`` validates a NULL into a required field.

Backfill any stray NULLs to the model's default, then enforce NOT NULL so the two layers
agree. Self-healing: the backfill runs first, so the migration is safe on any existing data
(ORM-created rows never hold NULLs here, so in practice it touches zero rows).

Revision ID: a035_tighten_nullability
Revises: a034_strength_decline_shadow
Create Date: 2026-07-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a035_tighten_nullability"
down_revision: str | None = "a034_strength_decline_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, existing_type, backfill SQL literal for the model's default)
_COLUMNS: list[tuple[str, str, sa.types.TypeEngine[object], str]] = [
    ("users", "is_active", sa.Boolean(), "true"),
    ("users", "created_at", sa.DateTime(), "(now() at time zone 'utc')"),
    ("athlete_profiles", "created_at", sa.DateTime(), "(now() at time zone 'utc')"),
    ("athlete_profiles", "updated_at", sa.DateTime(), "(now() at time zone 'utc')"),
    ("athlete_profiles", "experience_years", sa.Float(), "0.0"),
    ("athlete_profiles", "experience_level", sa.String(), "'beginner'"),
    ("athlete_profiles", "available_days_per_week", sa.Integer(), "3"),
    ("athlete_profiles", "session_duration_minutes", sa.Integer(), "60"),
    ("athlete_profiles", "equipment", postgresql.ARRAY(sa.String()), "'{}'"),
]


def upgrade() -> None:
    for table, column, existing_type, default_sql in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = {default_sql} WHERE {column} IS NULL")  # noqa: S608
        op.alter_column(table, column, existing_type=existing_type, nullable=False)


def downgrade() -> None:
    for table, column, existing_type, _default_sql in _COLUMNS:
        op.alter_column(table, column, existing_type=existing_type, nullable=True)
