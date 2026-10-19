"""a063 on a populated a062 database: existing profiles keep every value and read as unknown zone,
the shape CHECK holds, and the downgrade refuses while it would discard a zone (B1a).

Runs the real migration chain against a throwaway database (the a052/a061/a062 pattern).
"""
import os

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)
_BEFORE = "a062_shadow_log_provenance"
_HEAD = "a063_athlete_timezone"
_NEW = ("timezone", "timezone_source")


def _sync_url(database: str) -> str:
    return (
        make_url(_ASYNC_BASE)
        .set(drivername="postgresql+psycopg2", database=database)
        .render_as_string(hide_password=False)
    )


def _admin(sql: str) -> None:
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(sql))
    finally:
        admin.dispose()


def _has_column(conn: Connection, column: str) -> bool:
    return bool(conn.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'athlete_profiles' AND column_name = :c"
    ), {"c": column}).scalar_one())


def _athlete(conn: Connection, email: str) -> int:
    uid = conn.execute(text(
        "INSERT INTO users (email, hashed_password, is_active, created_at) "
        "VALUES (:e, 'h', true, now()) RETURNING id"
    ), {"e": email}).scalar_one()
    conn.execute(text(
        "INSERT INTO athlete_profiles (user_id, created_at, updated_at, experience_years, "
        "experience_level, available_days_per_week, session_duration_minutes, equipment, "
        "equipment_preference, onboarding_status, initial_seed_status, "
        "untracked_wellness_signals, date_of_birth) VALUES (:u, now(), now(), 2.0, "
        "'intermediate', 4, 60, '{barbell}', '{}', 'completed', 'none', '{}', '1990-03-04')"
    ), {"u": uid})
    return uid


def _set_zone(conn: Connection, uid: int, zone: str | None, source: str | None) -> None:
    conn.execute(text(
        "UPDATE athlete_profiles SET timezone = :z, timezone_source = :s WHERE user_id = :u"
    ), {"z": zone, "s": source, "u": uid})


def _refused(conn: Connection, uid: int, zone: str | None, source: str | None) -> None:
    with pytest.raises(IntegrityError, match="ck_athlete_profiles_timezone_shape"):
        with conn.begin_nested():
            _set_zone(conn, uid, zone, source)


def test_a063_keeps_profiles_unknown_and_guards_the_downgrade(_migrated_schema: None) -> None:
    probe_db = f"perflab_a063_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, _BEFORE)
            uid = _athlete(conn, "a063@test.com")
            snapshot = conn.execute(text("SELECT * FROM athlete_profiles ORDER BY id")).all()
            conn.commit()

            command.upgrade(cfg, _HEAD)
            after = conn.execute(text("SELECT * FROM athlete_profiles ORDER BY id")).mappings().all()
            # Every existing value survives, and every existing athlete reads as unknown zone.
            assert [tuple(r[c] for c in r if c not in _NEW) for r in after] == [
                tuple(r) for r in snapshot]
            assert all(r["timezone"] is None and r["timezone_source"] is None for r in after)

            # The shape: zone and source together, a known source, never an empty name.
            _refused(conn, uid, "America/New_York", None)
            _refused(conn, uid, None, "browser")
            _refused(conn, uid, "America/New_York", "guess")
            _refused(conn, uid, "", "athlete")
            _set_zone(conn, uid, "UTC", "athlete")  # explicit UTC is a known zone
            other = _athlete(conn, "a063-other@test.com")
            _set_zone(conn, other, "America/Los_Angeles", "browser")
            conn.commit()

            # A zone refuses the downgrade, which changes nothing.
            with pytest.raises(RuntimeError, match=r"2 athlete\(s\) have a timezone"):
                command.downgrade(cfg, _BEFORE)
            conn.rollback()
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _HEAD
            assert conn.execute(text(
                "SELECT timezone FROM athlete_profiles WHERE user_id = :u"), {"u": uid}
            ).scalar_one() == "UTC"

            # With the zones cleared deliberately, it runs and every other value survives.
            conn.execute(text("UPDATE athlete_profiles SET timezone = NULL, timezone_source = NULL"))
            conn.commit()
            command.downgrade(cfg, _BEFORE)
            assert not _has_column(conn, "timezone")
            assert not _has_column(conn, "timezone_source")
            first = conn.execute(text(
                "SELECT * FROM athlete_profiles WHERE user_id = :u"), {"u": uid}).all()
            assert [tuple(r) for r in first] == [tuple(r) for r in snapshot]

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
