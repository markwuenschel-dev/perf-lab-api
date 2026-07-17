"""Every migration's downgrade() actually runs (PA-18).

The model-drift gate (tests/test_migration_model_drift.py) only ever upgrades to head,
so a broken ``downgrade()`` — or a NOT NULL / constraint regression a downgrade fails to
reverse — shipped invisibly. This exercises the reverse direction end to end: upgrade to
head, downgrade to base, upgrade to head again.

It runs on a throwaway database (created + dropped here) so the shared, session-scoped
schema the rest of the suite depends on is never disturbed. Gated on DB availability via
``_migrated_schema`` (skips locally without a DB, hard-fails under REQUIRE_DB in CI).
"""
import os

from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)


def _sync_url(database: str) -> str:
    """The configured server/credentials as a synchronous (psycopg2) URL for `database`.

    Alembic's command API drives a sync connection; the app/test base URL is asyncpg."""
    return (
        make_url(_ASYNC_BASE)
        .set(drivername="postgresql+psycopg2", database=database)
        .render_as_string(hide_password=False)
    )


def _current_rev(conn: Connection) -> str | None:
    return conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()


def _drop_database(name: str) -> None:
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        admin.dispose()


def test_all_migrations_round_trip_head_to_base_to_head(_migrated_schema: None) -> None:
    # Per-worker name so xdist workers never collide on the throwaway database.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    probe_db = f"perflab_migration_roundtrip_{worker}"

    _drop_database(probe_db)
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{probe_db}"'))
    finally:
        admin.dispose()

    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn

            command.upgrade(cfg, "head")
            head = _current_rev(conn)
            assert head, "upgrade to head must set a revision"

            command.downgrade(cfg, "base")
            assert _current_rev(conn) is None, "downgrade to base must leave no applied revision"

            command.upgrade(cfg, "head")
            assert _current_rev(conn) == head, "re-upgrade must return to the same head"

            conn.commit()
    finally:
        engine.dispose()
        _drop_database(probe_db)
