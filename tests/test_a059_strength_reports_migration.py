"""a058 + a059: underived strength reports and strength submission keys (P4-3a, schema only).

A report holds a rep_set's facts and why nothing was derived from it; the reason is pinned to the
reported effort; its facts are permanent and its supersession is set at most once. A key is
scoped to its athlete, names exactly one effect, an effect belongs to at most one key, and keys
are append-only. Each downgrade refuses while its table holds a row; otherwise the schema returns
to a057 with the rows from before untouched.

Runs the real migration chain against a throwaway database (the a036 pattern).
"""
import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)

_REPORT_COLS = "user_id, benchmark_definition_id, method, collection_mode, load_kg, reps, rpe, rir, reason"
_REPORT = (
    f"INSERT INTO underived_strength_reports ({_REPORT_COLS}) "
    "VALUES (:u, :d, :method, :mode, :load, :reps, :rpe, :rir, :reason) RETURNING id"
)
_KEY = (
    "INSERT INTO strength_submission_keys (user_id, idempotency_key, payload_digest, payload, "
    "observation_id, report_id, outcome) "
    "VALUES (:u, :k, 'digest', CAST('{}' AS jsonb), :o, :r, CAST('{}' AS jsonb)) RETURNING id"
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


def _expect(conn, sql: str, params: dict, match: str) -> None:
    with pytest.raises(Exception, match=match):
        with conn.begin_nested():
            conn.execute(text(sql), params)


def _report(uid: int, did: int, **over) -> dict:
    row = {"u": uid, "d": did, "method": "rep_set", "mode": "retest", "load": 100.0, "reps": 5,
           "rpe": None, "rir": None, "reason": "effort_unknown"}
    row.update(over)
    return row


def test_a058_a059_guard_reports_and_keys_and_round_trip(_migrated_schema: None) -> None:
    probe_db = f"perflab_a059_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
    _admin(f'CREATE DATABASE "{probe_db}"')
    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "a057_replay_refusal")
            uid, other = (conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES (:e, 'h', true, now()) RETURNING id"), {"e": e}).scalar_one()
                for e in ("a059@test.com", "a059-other@test.com"))
            did = conn.execute(text(
                "INSERT INTO benchmark_definitions (code, name, domain, metric_type, unit, "
                "is_primary_anchor, is_derived_only, is_validator_only, better_direction, "
                "observation_weight, created_at) VALUES ('a059_code', 'n', 'd', 'load', 'kg', "
                "false, false, false, 'higher', 1.0, now()) RETURNING id"
            )).scalar_one()
            obs, obs2, obs_other = (conn.execute(text(
                "INSERT INTO benchmark_observations (user_id, benchmark_definition_id, observed_at, "
                "raw_value, validity_status, source) VALUES (:u, :d, now(), 100, 'valid', "
                "'benchmark_test') RETURNING id"
            ), {"u": owner, "d": did}).scalar_one() for owner in (uid, uid, other))
            conn.commit()

            command.upgrade(cfg, "a059_strength_submission_keys")
            assert {"underived_strength_reports", "strength_submission_keys"} <= _tables(conn)
            assert conn.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_underived_strength_reports_lookup'"
            )).scalar_one().endswith("(user_id, benchmark_definition_id, performed_at)")

            # Only a rep_set from an athlete entry, with a real set.
            _expect(conn, _REPORT, _report(uid, did, method="tested_max"),
                    "ck_underived_strength_reports_method")
            _expect(conn, _REPORT, _report(uid, did, mode="workout"),
                    "ck_underived_strength_reports_collection_mode")
            _expect(conn, _REPORT, _report(uid, did, load=0.0), "ck_underived_strength_reports_set")
            _expect(conn, _REPORT, _report(uid, did, reps=0), "ck_underived_strength_reports_set")
            # The request schema's bounds hold here too, nonfinite values included.
            for load in ("NaN", "Infinity", "-Infinity"):
                _expect(conn, _REPORT.replace(":load", "CAST(:load AS float8)"),
                        _report(uid, did, load=load), "ck_underived_strength_reports_set")
            for rpe, rir, name in ((11.0, 2.0, "rpe"), (0.99, 2.0, "rpe"), ("NaN", 2.0, "rpe"),
                                   (8.0, -1.0, "rir"), (8.0, 10.01, "rir"), (8.0, "NaN", "rir")):
                _expect(conn, _REPORT.replace(":rpe", "CAST(:rpe AS float8)")
                        .replace(":rir", "CAST(:rir AS float8)"),
                        _report(uid, did, reason="effort_conflict", rpe=rpe, rir=rir),
                        f"ck_underived_strength_reports_{name}")
            # The reason is pinned to the reported effort.
            _expect(conn, _REPORT, _report(uid, did, rpe=8.0), "ck_underived_strength_reports_reason")
            _expect(conn, _REPORT, _report(uid, did, rir=2.0), "ck_underived_strength_reports_reason")
            _expect(conn, _REPORT, _report(uid, did, reason="effort_conflict", rpe=8.0),
                    "ck_underived_strength_reports_reason")
            _expect(conn, _REPORT, _report(uid, did, reason="no_evidence"),
                    "ck_underived_strength_reports_reason")
            # Valid boundaries are accepted. Whether a pair conflicts is the resolver's call: the
            # database pins only the shape, so any in-range pair may carry effort_conflict.
            for rpe, rir in ((1.0, 0.0), (10.0, 10.0)):
                conn.execute(text(_REPORT), _report(uid, did, reason="effort_conflict", rpe=rpe, rir=rir))
            conn.execute(text(_REPORT), _report(uid, did, load=0.5, reps=1))
            rid = conn.execute(text(_REPORT), _report(uid, did)).scalar_one()
            rid2 = conn.execute(
                text(_REPORT), _report(uid, did, reason="effort_conflict", rpe=8.0, rir=0.0)
            ).scalar_one()
            assert conn.execute(text(
                "SELECT performed_at IS NULL AND created_at IS NOT NULL "
                "AND superseded_by_observation_id IS NULL FROM underived_strength_reports WHERE id = :r"
            ), {"r": rid}).scalar_one()

            # Facts are permanent.
            for column, value in (("load_kg", 105.0), ("reps", 4), ("performed_at", "2026-10-01"),
                                  ("created_at", "2026-10-01"), ("user_id", other)):
                _expect(conn, f"UPDATE underived_strength_reports SET {column} = :v WHERE id = :r",
                        {"v": value, "r": rid}, "facts are immutable")
            _expect(conn, "DELETE FROM underived_strength_reports WHERE id = :r", {"r": rid},
                    "underived strength reports are permanent")
            # Supersession: both columns together, then set once, and an observation supersedes once.
            _expect(conn, "UPDATE underived_strength_reports SET superseded_by_observation_id = :o "
                    "WHERE id = :r", {"o": obs, "r": rid}, "ck_underived_strength_reports_superseded")
            _supersede = ("UPDATE underived_strength_reports SET superseded_by_observation_id = :o, "
                          "superseded_at = now() WHERE id = :r")
            conn.execute(text(_supersede), {"o": obs, "r": rid})
            _expect(conn, _supersede, {"o": obs, "r": rid2},
                    "uq_underived_strength_reports_superseded_by")
            _expect(conn, _supersede, {"o": obs2, "r": rid}, "is already superseded")
            _expect(conn, "UPDATE underived_strength_reports SET superseded_by_observation_id = NULL, "
                    "superseded_at = NULL WHERE id = :r", {"r": rid}, "is already superseded")

            # Keys: exactly one effect, an effect per key, scoped to the athlete, append-only.
            k = uuid.uuid4()
            _expect(conn, _KEY, {"u": uid, "k": k, "o": None, "r": None},
                    "ck_strength_submission_keys_one_effect")
            _expect(conn, _KEY, {"u": uid, "k": k, "o": obs, "r": rid},
                    "ck_strength_submission_keys_one_effect")
            kid = conn.execute(text(_KEY), {"u": uid, "k": k, "o": obs, "r": None}).scalar_one()
            _expect(conn, _KEY, {"u": uid, "k": k, "o": obs2, "r": None},
                    "uq_strength_submission_keys_key")
            _expect(conn, _KEY, {"u": uid, "k": uuid.uuid4(), "o": obs, "r": None},
                    "uq_strength_submission_keys_observation")
            conn.execute(text(_KEY), {"u": uid, "k": uuid.uuid4(), "o": None, "r": rid2})
            _expect(conn, _KEY, {"u": uid, "k": uuid.uuid4(), "o": None, "r": rid2},
                    "uq_strength_submission_keys_report")
            # Keys are a per-athlete namespace: another athlete may use the same key for their own
            # observation. (The schema does not check effect ownership; the P4-3b writer must.)
            conn.execute(text(_KEY), {"u": other, "k": k, "o": obs_other, "r": None})
            _expect(conn, "UPDATE strength_submission_keys SET payload_digest = 'x' WHERE id = :i",
                    {"i": kid}, "strength submission keys are append-only")
            _expect(conn, "DELETE FROM strength_submission_keys WHERE id = :i", {"i": kid},
                    "strength submission keys are append-only")
            conn.commit()

            # Each downgrade refuses while its table holds a row.
            with pytest.raises(Exception, match="refusing to downgrade a059"):
                command.downgrade(cfg, "a058_underived_strength_reports")
            conn.rollback()
            conn.execute(text("ALTER TABLE strength_submission_keys "
                              "DISABLE TRIGGER trg_strength_submission_keys_append_only"))
            conn.execute(text("DELETE FROM strength_submission_keys"))
            conn.commit()
            command.downgrade(cfg, "a058_underived_strength_reports")
            assert "strength_submission_keys" not in _tables(conn)
            with pytest.raises(Exception, match="refusing to downgrade a058"):
                command.downgrade(cfg, "a057_replay_refusal")
            conn.rollback()
            conn.execute(text("ALTER TABLE underived_strength_reports "
                              "DISABLE TRIGGER trg_underived_strength_reports_guard"))
            conn.execute(text("DELETE FROM underived_strength_reports"))
            conn.commit()
            command.downgrade(cfg, "a057_replay_refusal")
            assert "underived_strength_reports" not in _tables(conn)
            assert conn.execute(text("SELECT count(*) FROM benchmark_observations")).scalar_one() == 3

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
