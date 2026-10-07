"""a053 on pre-change rows: legacy workouts and state rows keep NULL dispositions and source
links (P3c's repair detects those), the constraints refuse an unexplained record-only row or an
unknown basis, and the downgrade drops the columns cleanly.

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


def test_a053_keeps_legacy_rows_null_constrains_new_ones_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a053_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a052_missed_sessions")
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a053@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            wid = conn.execute(text(
                "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
                "duration_minutes, session_rpe) VALUES (:u, now(), now(), 'Running', 30, 6) RETURNING id"
            ), {"u": uid}).scalar_one()
            sid = conn.execute(text(
                "INSERT INTO athlete_states (user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, "
                "b_met_anaerobic, f_met_systemic, f_nm_peripheral, f_nm_central, f_struct_damage, "
                "s_struct_signal, habit_strength, skill_state) "
                "VALUES (:u, now(), 50, 500, 50, 50, 0, 0, 0, 0, 0, 0, '{}') RETURNING id"
            ), {"u": uid}).scalar_one()
            conn.commit()

            command.upgrade(cfg, "a053_workout_state_disposition")

            legacy = conn.execute(text(
                "SELECT state_disposition, state_disposition_reason, timestamp_basis, "
                "client_timestamp, received_at FROM workout_logs WHERE id = :i"
            ), {"i": wid}).one()
            assert tuple(legacy) == (None, None, None, None, None)
            links = conn.execute(text(
                "SELECT source_workout_log_id, source_observation_id FROM athlete_states WHERE id = :i"
            ), {"i": sid}).one()
            assert tuple(links) == (None, None)

            for sql, constraint in [
                ("UPDATE workout_logs SET state_disposition = 'record_only' WHERE id = :i",
                 "ck_workout_logs_record_only_has_reason"),
                ("UPDATE workout_logs SET state_disposition = 'maybe' WHERE id = :i",
                 "ck_workout_logs_state_disposition"),
                ("UPDATE workout_logs SET timestamp_basis = 'device' WHERE id = :i",
                 "ck_workout_logs_timestamp_basis"),
            ]:
                with pytest.raises(Exception, match=constraint):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"i": wid})
            conn.execute(text(
                "UPDATE workout_logs SET state_disposition = 'record_only', "
                "state_disposition_reason = 'event_before_current_state' WHERE id = :i"
            ), {"i": wid})
            conn.execute(text(
                "UPDATE athlete_states SET source_workout_log_id = :w WHERE id = :s"
            ), {"w": wid, "s": sid})
            conn.commit()

            command.downgrade(cfg, "a052_missed_sessions")
            assert "state_disposition" not in _columns(conn, "workout_logs")
            assert "source_workout_log_id" not in _columns(conn, "athlete_states")
            assert conn.execute(text("SELECT count(*) FROM workout_logs")).scalar_one() == 1

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
