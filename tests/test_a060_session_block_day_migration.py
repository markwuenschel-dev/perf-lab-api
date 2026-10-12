"""a060 on pre-change rows: the slot is copied before ``day_of_week`` is relabeled, the downgrade
restores every session's recorded slot exactly (rows written after the upgrade included), and an
out-of-range legacy value refuses the upgrade without changing anything.

Fixtures are adversarial on purpose: a block that starts on a Thursday (so slots and weekdays
disagree), a session moved before ``original_scheduled_date`` existed (NULL original, and its
recorded slot disagrees with the slot its current date implies), a modern reschedule, and
sessions created and moved after the upgrade. Dates, status, week numbers and prescriptions must
come through every step unchanged.

Runs the real migration chain against a throwaway database (the a036 pattern).
"""
import os
from datetime import date

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)
_BEFORE = "a059_strength_submission_keys"
_A060 = "a060_session_block_day"
THURSDAY = date(2026, 10, 15)


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


def _old_session(conn: Connection, bid: int, uid: int, on: str, week: int, slot: int, *,
                 original: str | None = None, status: str = "pending") -> int:
    """A session as the pre-a060 schema stores it: the slot in day_of_week."""
    return conn.execute(text(
        "INSERT INTO planned_sessions (block_id, user_id, scheduled_date, original_scheduled_date, "
        "week_number, day_of_week, category, modality, status, prescribed_content) VALUES "
        "(:b, :u, CAST(:on AS date), CAST(:orig AS date), :w, :slot, 'Heavy Lower', 'Strength', "
        "CAST(:st AS sessionstatus), CAST(:rx AS jsonb)) RETURNING id"
    ), {"b": bid, "u": uid, "on": on, "orig": original, "w": week, "slot": slot, "st": status,
        "rx": f'{{"focus": "slot {slot} week {week}"}}'}).scalar_one()


def _new_session(conn: Connection, bid: int, uid: int, on: str, week: int, slot: int) -> int:
    """A session as P5a's block creation writes it: the slot in block_day, the weekday in
    day_of_week."""
    return conn.execute(text(
        "INSERT INTO planned_sessions (block_id, user_id, scheduled_date, week_number, block_day, "
        "day_of_week, category, modality, status) VALUES (:b, :u, CAST(:on AS date), :w, :slot, "
        "EXTRACT(ISODOW FROM CAST(:on AS date))::int, 'Heavy Lower', 'Strength', 'pending') "
        "RETURNING id"
    ), {"b": bid, "u": uid, "on": on, "w": week, "slot": slot}).scalar_one()


def _move(conn: Connection, sid: int, to: str) -> None:
    """What update_session does: keep the first plan date, change only the date."""
    conn.execute(text(
        "UPDATE planned_sessions SET original_scheduled_date = "
        "COALESCE(original_scheduled_date, scheduled_date), scheduled_date = CAST(:to AS date) "
        "WHERE id = :i"
    ), {"i": sid, "to": to})


def _untouched(conn: Connection) -> list[tuple]:
    """Everything a060 must never change."""
    sessions = conn.execute(text(
        "SELECT id, block_id, scheduled_date, original_scheduled_date, week_number, status::text, "
        "category, is_deload, is_benchmark, prescribed_content::text, current_revision_id "
        "FROM planned_sessions ORDER BY id"
    )).all()
    revisions = conn.execute(text(
        "SELECT id, planned_session_id, revision_no, content::text, content_hash "
        "FROM prescription_revisions ORDER BY id"
    )).all()
    return [tuple(r) for r in sessions] + [tuple(r) for r in revisions]


def _days(conn: Connection, column: str) -> dict[int, int]:
    return dict(conn.execute(text(f"SELECT id, {column} FROM planned_sessions")).all())


def _has_block_day(conn: Connection) -> bool:
    return conn.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'planned_sessions' AND column_name = 'block_day'"
    )).scalar_one() == 1


def _checks(conn: Connection) -> set[str]:
    return set(conn.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'planned_sessions'::regclass "
        "AND contype = 'c'"
    )).scalars())


def _revision(conn: Connection) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _weekday(on: str) -> int:
    return date.fromisoformat(on).isoweekday()


def test_a060_keeps_the_recorded_slot_and_round_trips_exactly(_migrated_schema: None) -> None:
    probe_db = f"perflab_a060_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
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
                "VALUES ('a060@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            bid = conn.execute(text(
                "INSERT INTO mesocycle_blocks (user_id, goal, status, duration_weeks, "
                "sessions_per_week, start_date, modality_mix, weekly_template) VALUES "
                "(:u, 'Strength', 'active', 4, 3, CAST(:s AS date), '{}', '[]') RETURNING id"
            ), {"u": uid, "s": THURSDAY.isoformat()}).scalar_one()

            # Before a060. Block starts Thursday 2026-10-15; slots 1/3/5 land Thu/Sat/Mon.
            done = _old_session(conn, bid, uid, "2026-10-15", 1, 1, status="completed")
            sat = _old_session(conn, bid, uid, "2026-10-17", 1, 3)
            # Moved before a003 recorded original dates: planned Mon 10-19 (slot 5), now Wed 10-21
            # with no original. Its date implies slot 7; the recorded slot is 5.
            legacy = _old_session(conn, bid, uid, "2026-10-21", 1, 5)
            # A modern reschedule: planned Thu 10-22 (week 2, slot 1), moved to Fri 10-23.
            modern = _old_session(conn, bid, uid, "2026-10-23", 2, 1, original="2026-10-22")
            conn.execute(text(
                "INSERT INTO prescription_revisions (planned_session_id, user_id, revision_no, "
                "content, content_hash, reason, origin) VALUES (:s, :u, 1, '{\"focus\": \"squat\"}', "
                "'h1', 'initial', 'server')"
            ), {"s": done, "u": uid})
            conn.execute(text(
                "UPDATE planned_sessions SET current_revision_id = "
                "(SELECT id FROM prescription_revisions WHERE planned_session_id = :s) WHERE id = :s"
            ), {"s": done})
            conn.commit()
            slots_before = _days(conn, "day_of_week")
            assert slots_before == {done: 1, sat: 3, legacy: 5, modern: 1}
            baseline = _untouched(conn)

            command.upgrade(cfg, _A060)
            conn.commit()
            assert _days(conn, "block_day") == slots_before
            assert _days(conn, "day_of_week") == {
                done: _weekday("2026-10-15"),  # Thursday
                sat: _weekday("2026-10-17"),  # Saturday
                # No plan date survives: the weekday of the date it was moved to (Wednesday). Not a
                # verified plan weekday.
                legacy: _weekday("2026-10-21"),
                modern: _weekday("2026-10-22"),  # the plan date's Thursday, not the move's Friday
            }
            assert _untouched(conn) == baseline
            assert {"ck_planned_sessions_block_day", "ck_planned_sessions_day_of_week"} <= _checks(conn)

            # After a060, as P5a writes: a new session, and a new session that is then moved.
            fresh = _new_session(conn, bid, uid, "2026-10-24", 2, 3)
            fresh_moved = _new_session(conn, bid, uid, "2026-10-26", 2, 5)
            _move(conn, fresh_moved, "2026-10-28")
            _move(conn, sat, "2026-10-18")  # a pre-a060 session moved after the upgrade
            conn.commit()
            assert _days(conn, "day_of_week")[fresh_moved] == _weekday("2026-10-26")
            after_upgrade = _untouched(conn)

            # Downgrade: every session gets back the slot it was generated from.
            command.downgrade(cfg, _BEFORE)
            conn.commit()
            assert _revision(conn) == _BEFORE
            assert not _has_block_day(conn)
            assert not {"ck_planned_sessions_block_day", "ck_planned_sessions_day_of_week"} & _checks(conn)
            assert _days(conn, "day_of_week") == {
                done: 1, sat: 3, legacy: 5, modern: 1, fresh: 3, fresh_moved: 5,
            }
            assert _untouched(conn) == after_upgrade
            assert conn.execute(text(
                "SELECT col_description('planned_sessions'::regclass, attnum) FROM pg_attribute "
                "WHERE attrelid = 'planned_sessions'::regclass AND attname = 'day_of_week'"
            )).scalar_one() is None

            # The pre-a060 app writes again (slot in day_of_week), and moves one of its sessions.
            old_app = _old_session(conn, bid, uid, "2026-10-29", 3, 1)
            _move(conn, old_app, "2026-10-30")
            conn.commit()

            # Re-upgrade: every population gets its slot and its plan date's weekday.
            command.upgrade(cfg, _A060)
            conn.commit()
            assert _days(conn, "block_day") == {
                done: 1, sat: 3, legacy: 5, modern: 1, fresh: 3, fresh_moved: 5, old_app: 1,
            }
            assert _days(conn, "day_of_week") == {
                done: _weekday("2026-10-15"),
                sat: _weekday("2026-10-17"),  # moved to Sunday; its plan date was Saturday
                legacy: _weekday("2026-10-21"),
                modern: _weekday("2026-10-22"),
                fresh: _weekday("2026-10-24"),
                fresh_moved: _weekday("2026-10-26"),
                old_app: _weekday("2026-10-29"),
            }
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')


def test_a060_refuses_an_out_of_range_day_and_changes_nothing(_migrated_schema: None) -> None:
    probe_db = f"perflab_a060_refusal_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
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
                "VALUES ('a060-refusal@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            bid = conn.execute(text(
                "INSERT INTO mesocycle_blocks (user_id, goal, status, duration_weeks, "
                "sessions_per_week, start_date, modality_mix, weekly_template) VALUES "
                "(:u, 'Strength', 'active', 4, 3, CAST(:s AS date), '{}', '[]') RETURNING id"
            ), {"u": uid, "s": THURSDAY.isoformat()}).scalar_one()
            good = _old_session(conn, bid, uid, "2026-10-17", 1, 3)
            bad = _old_session(conn, bid, uid, "2026-10-15", 1, 0)
            conn.commit()

            with pytest.raises(RuntimeError, match=r"Refusing to upgrade to a060: 1 planned session"):
                command.upgrade(cfg, _A060)
            conn.rollback()
            assert _revision(conn) == _BEFORE
            assert not _has_block_day(conn)
            assert _days(conn, "day_of_week") == {good: 3, bad: 0}  # nothing relabeled

            conn.execute(text("UPDATE planned_sessions SET day_of_week = 1 WHERE id = :i"), {"i": bad})
            conn.commit()
            command.upgrade(cfg, _A060)
            conn.commit()
            assert _days(conn, "block_day") == {good: 3, bad: 1}
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
