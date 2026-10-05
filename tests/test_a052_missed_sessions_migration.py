"""a052 on pre-change rows: the enum value, the feedback backfill, the partial unique index,
and a downgrade that refuses to delete feedback and returns ``missed`` sessions to ``pending``.

Runs the real migration chain against a throwaway database (the a036 pattern).
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
_BEFORE = "a051_prescription_revisions"


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


def _session(conn: Connection, block_id: int, user_id: int, status: str, *, moved: bool = False) -> int:
    return conn.execute(
        text(
            "INSERT INTO planned_sessions (block_id, user_id, scheduled_date, original_scheduled_date, "
            "week_number, day_of_week, category, modality, status) VALUES (:b, :u, DATE '2026-10-02', "
            ":orig, 1, 5, 'Heavy Lower', 'Strength', CAST(:s AS sessionstatus)) RETURNING id"
        ),
        {"b": block_id, "u": user_id, "s": status, "orig": "2026-09-30" if moved else None},
    ).scalar_one()


def _feedback(conn: Connection, session_id: int, status: str) -> int:
    return conn.execute(
        text(
            "INSERT INTO session_feedback (planned_session_id, status, created_at) "
            "VALUES (:s, :st, now()) RETURNING id"
        ),
        {"s": session_id, "st": status},
    ).scalar_one()


def _feedback_state(conn: Connection, feedback_id: int) -> tuple[str | None, bool]:
    row = conn.execute(
        text("SELECT describes_status, superseded_at IS NOT NULL FROM session_feedback WHERE id = :i"),
        {"i": feedback_id},
    ).one()
    return row[0], row[1]


def test_a052_backfills_feedback_and_round_trips(_migrated_schema: None) -> None:
    probe_db = f"perflab_a052_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, _BEFORE)

            uid = conn.execute(
                text(
                    "INSERT INTO users (email, hashed_password, is_active, created_at) "
                    "VALUES ('a052@test.com', 'h', true, now()) RETURNING id"
                )
            ).scalar_one()
            bid = conn.execute(
                text(
                    "INSERT INTO mesocycle_blocks (user_id, goal, status, duration_weeks, "
                    "sessions_per_week, start_date, modality_mix, weekly_template) VALUES "
                    "(:u, 'Strength', 'active', 4, 3, DATE '2026-09-28', '{}', '[]') RETURNING id"
                ),
                {"u": uid},
            ).scalar_one()
            completed = _session(conn, bid, uid, "completed")
            skipped = _session(conn, bid, uid, "skipped")
            reopened = _session(conn, bid, uid, "pending")  # pre-F3: skipped, fed back, reopened
            moved = _session(conn, bid, uid, "rescheduled", moved=True)
            fb_modified = _feedback(conn, completed, "modified")
            fb_skipped = _feedback(conn, skipped, "skipped")
            fb_stranded = _feedback(conn, reopened, "skipped")
            fb_unknown = _feedback(conn, moved, "unknown")
            conn.commit()

            command.upgrade(cfg, "a052_missed_sessions")

            # What the feedback says wins; "unknown" falls back to the session's status.
            assert _feedback_state(conn, fb_modified) == ("completed", False)
            assert _feedback_state(conn, fb_skipped) == ("skipped", False)
            # Describing an outcome the session no longer has: kept, superseded.
            assert _feedback_state(conn, fb_stranded) == ("skipped", True)
            assert _feedback_state(conn, fb_unknown) == ("rescheduled", False)
            assert conn.execute(
                text("SELECT original_scheduled_date FROM planned_sessions WHERE id = :i"), {"i": moved}
            ).scalar_one().isoformat() == "2026-09-30"

            # New feedback may follow superseded feedback; two ACTIVE rows may not coexist.
            _feedback(conn, reopened, "unknown")
            with pytest.raises(Exception, match="uq_session_feedback_active_per_session"):
                with conn.begin_nested():
                    _feedback(conn, skipped, "unknown")

            missed = _session(conn, bid, uid, "missed")
            conn.commit()

            # A session now has two feedback rows: the downgrade refuses rather than delete one.
            with pytest.raises(RuntimeError, match="more than one feedback row"):
                command.downgrade(cfg, _BEFORE)
            conn.rollback()
            conn.execute(text("DELETE FROM session_feedback WHERE id = :i"), {"i": fb_stranded})
            conn.commit()

            command.downgrade(cfg, _BEFORE)
            assert conn.execute(
                text("SELECT status::text FROM planned_sessions WHERE id = :i"), {"i": missed}
            ).scalar_one() == "pending"
            labels = conn.execute(
                text(
                    "SELECT array_agg(e.enumlabel::text ORDER BY e.enumsortorder) FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'sessionstatus'"
                )
            ).scalar_one()
            assert labels == ["pending", "completed", "skipped", "rescheduled"]
            with pytest.raises(Exception, match="session_feedback_planned_session_id_key"):
                with conn.begin_nested():
                    _feedback(conn, skipped, "unknown")

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
