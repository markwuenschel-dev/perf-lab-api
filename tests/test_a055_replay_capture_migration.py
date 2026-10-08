"""a055 on pre-change rows: legacy state rows, workouts and observations keep NULL capture (they
are not replayable, and nothing pretends otherwise); the constraints and the immutability trigger
bite on new rows; the downgrade removes the capture and leaves the legacy data alone.

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


def _tables(conn) -> set[str]:
    return set(conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )).scalars())


def _expect(conn, sql: str, params: dict, match: str) -> None:
    with pytest.raises(Exception, match=match):
        with conn.begin_nested():
            conn.execute(text(sql), params)


def test_a055_leaves_legacy_rows_uncaptured_guards_new_ones_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a055_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a054_benchmark_state_disposition")
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a055@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            sid = conn.execute(text(
                "INSERT INTO athlete_states (user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, "
                "b_met_anaerobic, f_met_systemic, f_nm_peripheral, f_nm_central, f_struct_damage, "
                "s_struct_signal, habit_strength, skill_state) "
                "VALUES (:u, now(), 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, '{}'::jsonb) RETURNING id"
            ), {"u": uid}).scalar_one()
            wid = conn.execute(text(
                "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
                "duration_minutes, session_rpe) VALUES (:u, now(), now(), 'Running', 30, 6) RETURNING id"
            ), {"u": uid}).scalar_one()
            did = conn.execute(text(
                "INSERT INTO benchmark_definitions (code, name, domain, metric_type, unit, "
                "is_primary_anchor, is_derived_only, is_validator_only, better_direction, "
                "observation_weight, created_at) VALUES ('a055_code', 'n', 'd', 'load', 'kg', "
                "false, false, false, 'higher', 1.0, now()) RETURNING id"
            )).scalar_one()
            oid = conn.execute(text(
                "INSERT INTO benchmark_observations (user_id, benchmark_definition_id, observed_at, "
                "raw_value, validity_status, source) VALUES (:u, :d, now(), 100, 'valid', "
                "'benchmark_test') RETURNING id"
            ), {"u": uid, "d": did}).scalar_one()
            conn.commit()

            command.upgrade(cfg, "a055_replay_capture")

            # Legacy rows: no capture, and nothing invented for them.
            assert tuple(conn.execute(text(
                "SELECT event_kind, predecessor_state_id, transition_identity, anchored_from "
                "FROM athlete_states WHERE id = :i"), {"i": sid}).one()) == (None, None, None, None)
            assert conn.execute(text(
                "SELECT replay_input FROM workout_logs WHERE id = :i"), {"i": wid}).scalar_one() is None
            assert conn.execute(text(
                "SELECT replay_input FROM benchmark_observations WHERE id = :i"), {"i": oid}).scalar_one() is None
            assert conn.execute(text("SELECT count(*) FROM engine_transition_identities")).scalar_one() == 0

            # Constraints on new rows.
            _expect(conn, "UPDATE athlete_states SET event_kind = 'mystery' WHERE id = :i",
                    {"i": sid}, "ck_athlete_states_event_kind")
            for kind in ("workout", "benchmark"):
                _expect(conn, "UPDATE athlete_states SET event_kind = :k WHERE id = :i",
                        {"i": sid, "k": kind}, "ck_athlete_states_transition_has_identity")
            _expect(conn, "UPDATE athlete_states SET transition_identity = 'nope' WHERE id = :i",
                    {"i": sid}, "fk_athlete_states_transition_identity")
            conn.execute(text(
                "INSERT INTO engine_transition_identities (digest, components) VALUES ('d1', '{}'::jsonb)"))
            conn.execute(text(
                "UPDATE athlete_states SET event_kind = 'workout', transition_identity = 'd1', "
                "predecessor_state_id = :i WHERE id = :i"), {"i": sid})

            # Immutability: NULL → value once, then fixed.
            for table, row in (("workout_logs", wid), ("benchmark_observations", oid)):
                conn.execute(text(f"UPDATE {table} SET replay_input = '{{\"v\": 1}}'::jsonb WHERE id = :i"),
                             {"i": row})
                _expect(conn, f"UPDATE {table} SET replay_input = '{{\"v\": 2}}'::jsonb WHERE id = :i",
                        {"i": row}, "replay_input is immutable")
                _expect(conn, f"UPDATE {table} SET replay_input = NULL WHERE id = :i",
                        {"i": row}, "replay_input is immutable")
            conn.commit()

            command.downgrade(cfg, "a054_benchmark_state_disposition")
            assert {"event_kind", "predecessor_state_id", "transition_identity", "anchored_from"} \
                .isdisjoint(_columns(conn, "athlete_states"))
            assert "replay_input" not in _columns(conn, "workout_logs") | _columns(conn, "benchmark_observations")
            assert "engine_transition_identities" not in _tables(conn)
            assert conn.execute(text("SELECT count(*) FROM athlete_states")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM workout_logs")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM benchmark_observations")).scalar_one() == 1

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
