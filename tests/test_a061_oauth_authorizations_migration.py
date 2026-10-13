"""a061 on a populated a060 database: existing connections get generation 1, and the downgrade
refuses while it would discard attempt history or relink counts (P6a, ADR-0075).

Runs the real migration chain against a throwaway database (the a036/a052 pattern).
"""
import os

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)
_BEFORE = "a060_session_block_day"
_HEAD = "a061_oauth_authorizations"
_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


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


def _exists(conn: Connection, table: str, column: str | None = None) -> bool:
    if column is None:
        return conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar_one()
    return bool(conn.execute(text(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar_one())


def _refuses_and_changes_nothing(conn: Connection, cfg: Config, match: str) -> None:
    with pytest.raises(RuntimeError, match=match):
        command.downgrade(cfg, _BEFORE)
    conn.rollback()
    assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _HEAD
    assert _exists(conn, "oauth_authorizations")
    assert _exists(conn, "wearable_connections", "generation")


def test_a061_backfills_generation_and_refuses_to_discard_history(_migrated_schema: None) -> None:
    probe_db = f"perflab_a061_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, _BEFORE)
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a061@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            pat_uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a061-pat@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            for user, auth in ((uid, "oauth"), (pat_uid, "pat")):
                conn.execute(text(
                    "INSERT INTO wearable_connections (user_id, provider, auth_type, "
                    "access_token_enc, refresh_token_enc, created_at, updated_at) "
                    "VALUES (:u, 'oura', :a, 'enc-a', 'enc-r', now(), now())"
                ), {"u": user, "a": auth})
            before = conn.execute(text(
                "SELECT id, user_id, auth_type, access_token_enc, refresh_token_enc "
                "FROM wearable_connections ORDER BY id")).all()
            conn.commit()

            command.upgrade(cfg, _HEAD)
            rows = conn.execute(text(
                "SELECT id, user_id, auth_type, access_token_enc, refresh_token_enc, generation "
                "FROM wearable_connections ORDER BY id")).all()
            assert [tuple(r[:5]) for r in rows] == [tuple(r) for r in before]
            assert [r[5] for r in rows] == [1, 1]

            # An attempt row alone refuses the downgrade.
            conn.execute(text(
                "INSERT INTO oauth_authorizations (user_id, provider, state_hash, code_challenge, "
                "redirect_uri, status, created_at, expires_at) VALUES (:u, 'oura', :h, :c, "
                "'https://perflab.test/settings/oura-callback', 'pending', now(), "
                "now() + interval '10 minutes')"
            ), {"u": uid, "h": "a" * 64, "c": _CHALLENGE})
            conn.commit()
            _refuses_and_changes_nothing(conn, cfg, r"1 OAuth authorization attempt\(s\) and 0")

            # So does a relinked connection with no attempts left.
            conn.execute(text("DELETE FROM oauth_authorizations"))
            conn.execute(text("UPDATE wearable_connections SET generation = 2 WHERE user_id = :u"),
                         {"u": uid})
            conn.commit()
            _refuses_and_changes_nothing(conn, cfg, r"0 OAuth authorization attempt\(s\) and 1")

            # Empty history: the downgrade runs and leaves the connections as they were.
            conn.execute(text("UPDATE wearable_connections SET generation = 1"))
            conn.commit()
            command.downgrade(cfg, _BEFORE)
            assert not _exists(conn, "oauth_authorizations")
            assert not _exists(conn, "wearable_connections", "generation")
            assert conn.execute(text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'oauth_authorizations_guard'"
            )).scalar_one() == 0
            after = conn.execute(text(
                "SELECT id, user_id, auth_type, access_token_enc, refresh_token_enc "
                "FROM wearable_connections ORDER BY id")).all()
            assert [tuple(r) for r in after] == [tuple(r) for r in before]

            command.upgrade(cfg, "head")
            assert conn.execute(text(
                "SELECT array_agg(generation) FROM wearable_connections")).scalar_one() == [1, 1]
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
