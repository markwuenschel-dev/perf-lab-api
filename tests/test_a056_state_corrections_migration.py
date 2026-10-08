"""a056: correction receipts. Rows from before it are untouched; a correction head must carry its
receipt and an identity; receipts are immutable and introduce an event at most once; the
downgrade refuses while a receipt exists (it would orphan the corrected heads) and otherwise
returns the schema to a055.

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

_STATE_COLS = (
    "user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, b_met_anaerobic, "
    "f_met_systemic, f_nm_peripheral, f_nm_central, f_struct_damage, "
    "s_struct_signal, habit_strength, skill_state"
)
_STATE_VALS = ":u, now(), 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, CAST('{}' AS jsonb)"
_PLAIN_STATE = f"INSERT INTO athlete_states ({_STATE_COLS}) VALUES ({_STATE_VALS}) RETURNING id"
_CORRECTION_COLS = "event_kind, source_correction_id, transition_identity"
_CORRECTION_VALS = "'correction', :c, 'd1'"
_CORRECTION_STATE = (
    f"INSERT INTO athlete_states ({_STATE_COLS}, {_CORRECTION_COLS}, predecessor_state_id) "
    f"VALUES ({_STATE_VALS}, {_CORRECTION_VALS}, :h) RETURNING id"
)
_CORRECTION_STATE_NO_PRED = (
    f"INSERT INTO athlete_states ({_STATE_COLS}, {_CORRECTION_COLS}) "
    f"VALUES ({_STATE_VALS}, {_CORRECTION_VALS})"
)
_RECEIPT = (
    "INSERT INTO state_corrections (user_id, algorithm_version, transition_identity, "
    "checkpoint_state_id, head_before_state_id, affected_from) "
    "VALUES (:u, 'tail-replay-v1', 'd1', :c, :h, now()) RETURNING id"
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


def _tables(conn) -> set[str]:
    return set(conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )).scalars())


def _columns(conn, table: str) -> set[str]:
    return set(conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
    ).scalars())


def _expect(conn, sql: str, params: dict, match: str) -> None:
    with pytest.raises(Exception, match=match):
        with conn.begin_nested():
            conn.execute(text(sql), params)


def test_a056_guards_receipts_and_correction_heads_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a056_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a055_replay_capture")
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a056@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            conn.execute(text(
                "INSERT INTO engine_transition_identities (digest, components) "
                "VALUES ('d1', CAST('{}' AS jsonb))"))
            ck = conn.execute(text(_PLAIN_STATE), {"u": uid}).scalar_one()
            head = conn.execute(text(_PLAIN_STATE), {"u": uid}).scalar_one()
            w1 = conn.execute(text(
                "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
                "duration_minutes, session_rpe) VALUES (:u, now(), now(), 'Running', 30, 6) RETURNING id"
            ), {"u": uid}).scalar_one()
            conn.commit()

            command.upgrade(cfg, "a056_state_corrections")
            assert {"state_corrections", "state_correction_events"} <= _tables(conn)
            assert "source_correction_id" in _columns(conn, "athlete_states")
            assert conn.execute(text(
                "SELECT source_correction_id FROM athlete_states WHERE id = :i"), {"i": head}
            ).scalar_one() is None  # earlier rows are untouched

            # A correction head needs its receipt and an identity; a receipt link needs the kind.
            _expect(conn, "UPDATE athlete_states SET event_kind = 'correction' WHERE id = :i",
                    {"i": head}, "ck_athlete_states_correction_link")
            cid = conn.execute(text(_RECEIPT), {"u": uid, "c": ck, "h": head}).scalar_one()
            _expect(conn, "UPDATE athlete_states SET source_correction_id = :c WHERE id = :i",
                    {"i": head, "c": cid}, "ck_athlete_states_correction_link")
            _expect(conn, "UPDATE athlete_states SET event_kind = 'correction', "
                    "source_correction_id = :c WHERE id = :i",
                    {"i": head, "c": cid}, "ck_athlete_states_transition_has_identity")
            corr_head = conn.execute(
                text(_CORRECTION_STATE), {"u": uid, "c": cid, "h": head}
            ).scalar_one()
            # …and only one head per receipt.
            _expect(conn, _CORRECTION_STATE_NO_PRED, {"u": uid, "c": cid},
                    "uq_athlete_states_source_correction")

            # Receipt events: exactly one event each, and an event is introduced once, ever.
            _expect(conn, "INSERT INTO state_correction_events (correction_id, ordinal) VALUES (:c, 0)",
                    {"c": cid}, "ck_state_correction_events_one_event")
            conn.execute(text(
                "INSERT INTO state_correction_events (correction_id, ordinal, workout_log_id) "
                "VALUES (:c, 0, :w)"), {"c": cid, "w": w1})
            cid2 = conn.execute(text(_RECEIPT), {"u": uid, "c": ck, "h": corr_head}).scalar_one()
            _expect(conn, "INSERT INTO state_correction_events (correction_id, ordinal, workout_log_id) "
                    "VALUES (:c, 0, :w)", {"c": cid2, "w": w1}, "uq_state_correction_events_workout")
            _expect(conn, "INSERT INTO state_correction_events (correction_id, ordinal, workout_log_id) "
                    "VALUES (:c, 0, :w)", {"c": cid, "w": w1}, "uq_state_correction_events_ordinal")

            # Receipts are immutable.
            _expect(conn, "UPDATE state_corrections SET algorithm_version = 'x' WHERE id = :c",
                    {"c": cid}, "receipts are immutable")
            _expect(conn, "UPDATE state_correction_events SET ordinal = 5 WHERE correction_id = :c",
                    {"c": cid}, "receipts are immutable")
            conn.commit()

            # The downgrade refuses while a receipt exists.
            with pytest.raises(Exception, match="refusing to downgrade a056"):
                command.downgrade(cfg, "a055_replay_capture")
            conn.rollback()

            # Receipts and correction heads reference each other: peel them in dependency order.
            conn.execute(text("DELETE FROM state_correction_events"))
            conn.execute(text("DELETE FROM state_corrections WHERE id = :c"), {"c": cid2})
            conn.execute(text("DELETE FROM athlete_states WHERE source_correction_id IS NOT NULL"))
            conn.execute(text("DELETE FROM state_corrections"))
            conn.commit()
            command.downgrade(cfg, "a055_replay_capture")
            assert "state_corrections" not in _tables(conn)
            assert "source_correction_id" not in _columns(conn, "athlete_states")
            assert conn.execute(text("SELECT count(*) FROM athlete_states")).scalar_one() == 2

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
