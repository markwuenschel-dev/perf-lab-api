"""a057: ``replay_refusal`` on workouts and benchmark observations. Existing rows read NULL (a fold
was never attempted), the column is writable and re-writable (a retry overwrites it), and the
downgrade drops it without touching the rows.

Runs the real migration chain against a throwaway database (the a036 pattern).
"""
import os

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


def test_a057_leaves_rows_null_is_rewritable_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a057_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a056_state_corrections")
            uid = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a057@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            wid = conn.execute(text(
                "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
                "duration_minutes, session_rpe) VALUES (:u, now(), now(), 'Running', 30, 6) RETURNING id"
            ), {"u": uid}).scalar_one()
            conn.commit()

            command.upgrade(cfg, "a057_replay_refusal")

            assert {"replay_refusal"} <= _columns(conn, "workout_logs") & _columns(conn, "benchmark_observations")
            assert conn.execute(
                text("SELECT replay_refusal FROM workout_logs WHERE id = :i"), {"i": wid}
            ).scalar_one() is None
            for code in ("window_exceeded", "untrusted_checkpoint", None):
                conn.execute(text("UPDATE workout_logs SET replay_refusal = :c WHERE id = :i"),
                             {"c": code, "i": wid})
                assert conn.execute(
                    text("SELECT replay_refusal FROM workout_logs WHERE id = :i"), {"i": wid}
                ).scalar_one() == code
            conn.commit()

            command.downgrade(cfg, "a056_state_corrections")
            assert "replay_refusal" not in _columns(conn, "workout_logs")
            assert "replay_refusal" not in _columns(conn, "benchmark_observations")
            assert conn.execute(text("SELECT count(*) FROM workout_logs")).scalar_one() == 1

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
