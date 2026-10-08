"""a054 on pre-change rows: legacy benchmark observations keep a NULL disposition, the
constraints refuse an unknown disposition or an unexplained record-only row, and the downgrade
drops the columns cleanly.

Runs the real migration chain against a throwaway database (the a036 pattern).
"""
import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)


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


def _columns(conn, table: str) -> set[str]:
    return set(conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
    ).scalars())


def test_a054_keeps_legacy_observations_null_constrains_new_ones_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a054_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a053_workout_state_disposition")
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a054@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            did = conn.execute(text(
                "INSERT INTO benchmark_definitions (code, name, domain, metric_type, unit, "
                "is_primary_anchor, is_derived_only, is_validator_only, better_direction, "
                "observation_weight, created_at) VALUES ('a054_code', 'n', 'd', 'load', 'kg', "
                "false, false, false, 'higher', 1.0, now()) RETURNING id"
            )).scalar_one()
            oid = conn.execute(text(
                "INSERT INTO benchmark_observations (user_id, benchmark_definition_id, observed_at, "
                "raw_value, validity_status, source) VALUES (:u, :d, now(), 100, 'valid', "
                "'benchmark_test') RETURNING id"
            ), {"u": uid, "d": did}).scalar_one()
            conn.commit()

            command.upgrade(cfg, "a054_benchmark_state_disposition")

            legacy = conn.execute(text(
                "SELECT state_disposition, state_disposition_reason FROM benchmark_observations WHERE id = :i"
            ), {"i": oid}).one()
            assert tuple(legacy) == (None, None)
            for sql, constraint in [
                ("UPDATE benchmark_observations SET state_disposition = 'record_only' WHERE id = :i",
                 "ck_benchmark_observations_record_only_has_reason"),
                ("UPDATE benchmark_observations SET state_disposition = 'maybe' WHERE id = :i",
                 "ck_benchmark_observations_state_disposition"),
            ]:
                with pytest.raises(Exception, match=constraint):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"i": oid})
            conn.execute(text(
                "UPDATE benchmark_observations SET state_disposition = 'record_only', "
                "state_disposition_reason = 'event_before_current_state' WHERE id = :i"
            ), {"i": oid})
            conn.commit()

            command.downgrade(cfg, "a053_workout_state_disposition")
            assert "state_disposition" not in _columns(conn, "benchmark_observations")
            assert conn.execute(text("SELECT count(*) FROM benchmark_observations")).scalar_one() == 1

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
