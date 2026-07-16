"""AUD-C12: columns the ORM models declare NOT NULL must be NOT NULL in the live schema.

The models declare these nine columns non-Optional (``Mapped[bool]`` / ``Mapped[datetime]`` /
…), so the app treats them as mandatory — ``ProfileRead`` requires them, and a NULL there 500s
``GET /v1/profile``. a035 backfilled and enforced NOT NULL to match. This pins that alignment:
if a future migration re-loosens any of them (or a000 is edited), the model↔DB contract has
silently drifted again and this fails. (Requires a DB.)
"""
import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.asyncio

# Every column whose model annotation is non-Optional but which a000 created nullable=True.
_MODEL_NOT_NULL: list[tuple[str, str]] = [
    ("users", "is_active"),
    ("users", "created_at"),
    ("athlete_profiles", "created_at"),
    ("athlete_profiles", "updated_at"),
    ("athlete_profiles", "experience_years"),
    ("athlete_profiles", "experience_level"),
    ("athlete_profiles", "available_days_per_week"),
    ("athlete_profiles", "session_duration_minutes"),
    ("athlete_profiles", "equipment"),
]


async def test_model_not_null_columns_are_enforced_by_the_db(async_db) -> None:
    result = await async_db.execute(
        sa.text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name IN ('users', 'athlete_profiles') "
            "AND is_nullable = 'YES'"
        )
    )
    nullable_in_db = {(row.table_name, row.column_name) for row in result}
    still_nullable = sorted(col for col in _MODEL_NOT_NULL if col in nullable_in_db)
    assert not still_nullable, (
        "column(s) the model declares NOT NULL are still nullable in the database — the "
        f"model↔DB nullability contract has drifted: {still_nullable}. A NULL in any of these "
        "breaks the read path (e.g. ProfileRead → 500)."
    )
