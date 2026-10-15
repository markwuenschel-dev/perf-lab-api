"""a062 on a populated a061 database: legacy shadow rows stay unlinked and intact, the correction
chain keys hold on both tables, and the downgrade refuses while it would discard links (P7a).

Runs the real migration chain against a throwaway database (the a036/a052/a061 pattern).
"""
import os

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from alembic import command

_ASYNC_BASE = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab_test",
)
_BEFORE = "a061_oauth_authorizations"
_HEAD = "a062_shadow_log_provenance"
_PROVENANCE = ("source_wellness_sample_id", "content_hash", "supersedes_id")


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


def _has_column(conn: Connection, table: str, column: str) -> bool:
    return bool(conn.execute(text(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar_one())


def _linked(sample: int | None, row_id: int | None) -> tuple[str, str]:
    cols = "" if sample is None else ", source_wellness_sample_id, content_hash, supersedes_id"
    vals = "" if sample is None else ", :s, :h, :p"
    if row_id is not None:
        cols, vals = cols + ", id", vals + ", :id"
    return cols, vals


def _recovery(conn: Connection, uid: int, *, sample: int | None = None, mv: str = "v1",
              digest: str | None = None, supersedes: int | None = None,
              row_id: int | None = None) -> int:
    linked, values = _linked(sample, row_id)
    return conn.execute(text(
        "INSERT INTO recovery_shadow_log (user_id, created_at, model_version, wellness, "
        f"fatigue_before, baseline_clearance_multiplier, learned_clearance_multiplier{linked}) "
        f"VALUES (:u, now(), :mv, '{{}}', '{{}}', '{{}}', '{{}}'{values}) RETURNING id"
    ), {"u": uid, "mv": mv, "s": sample, "h": digest, "p": supersedes,
        "id": row_id}).scalar_one()


def _personalization(conn: Connection, uid: int, *, sample: int | None = None, mv: str = "v1",
                     parameter: str = "recovery_beta", digest: str | None = None,
                     supersedes: int | None = None, row_id: int | None = None) -> int:
    linked, values = _linked(sample, row_id)
    return conn.execute(text(
        "INSERT INTO personalization_shadow_log (user_id, created_at, parameter, model_version, "
        f"wellness, population_multiplier, personalized_multiplier{linked}) "
        f"VALUES (:u, now(), :param, :mv, '{{}}', '{{}}', '{{}}'{values}) RETURNING id"
    ), {"u": uid, "mv": mv, "param": parameter, "s": sample, "h": digest,
        "p": supersedes, "id": row_id}).scalar_one()


def _refused(conn: Connection, constraint: str, insert) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        with conn.begin_nested():
            insert()


def _sample(conn: Connection, uid: int, day: str, source: str = "oura") -> int:
    return conn.execute(text(
        "INSERT INTO wellness_samples (user_id, date, source, created_at) "
        "VALUES (:u, :d, :s, now()) RETURNING id"
    ), {"u": uid, "d": day, "s": source}).scalar_one()


def _chain_keys_hold(conn: Connection, uid: int, short: str, write, s1: int, s2: int,
                     other: int, s_other: int) -> None:
    """The shape CHECK and every chain key on one table, then A → B → A on one sample, then
    cycles and cross-athlete links, written by INSERT and by UPDATE."""
    _refused(conn, f"ck_{short}_provenance_shape", lambda: write(conn, uid, sample=s1))
    _refused(conn, f"ck_{short}_provenance_shape", lambda: conn.execute(text(
        f"INSERT INTO {short}_log (user_id, created_at, model_version, wellness, "
        + ("fatigue_before, baseline_clearance_multiplier, learned_clearance_multiplier, "
           if short == "recovery_shadow" else "population_multiplier, personalized_multiplier, ")
        + "content_hash) VALUES (:u, now(), 'v1', '{}', '{}', '{}', "
        + ("'{}', " if short == "recovery_shadow" else "")
        + "'h')"), {"u": uid}))

    a = write(conn, uid, sample=s1, digest="A")
    _refused(conn, f"uq_{short}_chain_root", lambda: write(conn, uid, sample=s1, digest="B"))
    other_model = write(conn, uid, sample=s1, mv="v2", digest="A")  # another model: its own chain
    other_sample = write(conn, uid, sample=s2, digest="A")
    b = write(conn, uid, sample=s1, digest="B", supersedes=a)
    _refused(conn, f"uq_{short}_supersedes_once",
             lambda: write(conn, uid, sample=s1, digest="C", supersedes=a))
    # A link never leaves its chain: not to another sample's row, nor to another model's.
    _refused(conn, f"fk_{short}_supersedes_same_chain",
             lambda: write(conn, uid, sample=s1, digest="C", supersedes=other_sample))
    _refused(conn, f"fk_{short}_supersedes_same_chain",
             lambda: write(conn, uid, sample=s1, digest="C", supersedes=other_model))
    # Corrected back to A: the head is B, so A is a new row, not a collision with the root.
    back = write(conn, uid, sample=s1, digest="A", supersedes=b)
    heads = conn.execute(text(
        f"SELECT id FROM {short}_log r WHERE source_wellness_sample_id = :s AND model_version = 'v1' "
        f"AND NOT EXISTS (SELECT 1 FROM {short}_log n WHERE n.supersedes_id = r.id)"
    ), {"s": s1}).scalars().all()
    assert heads == [back]

    table = f"{short}_log"
    older = f"ck_{short}_supersedes_older"
    # No cycle of any length: a self-link, a root turned into a successor of its own successor,
    # and an INSERT that names a row newer than itself are all refused. Each case is built so
    # that this CHECK is the only key it breaks (nothing else supersedes the rows it names).
    _refused(conn, older, lambda: conn.execute(text(
        f"UPDATE {table} SET supersedes_id = id WHERE id = :r"), {"r": back}))
    s2_next = write(conn, uid, sample=s2, digest="B", supersedes=other_sample)
    _refused(conn, older, lambda: conn.execute(text(
        f"UPDATE {table} SET supersedes_id = :n WHERE id = :r"),
        {"n": s2_next, "r": other_sample}))
    _refused(conn, older, lambda: write(conn, uid, sample=s1, digest="C", supersedes=back,
                                        row_id=0))

    # Another athlete can name neither this athlete's sample nor this athlete's rows.
    source_fk = f"fk_{short}_source_wellness_sample"
    _refused(conn, source_fk, lambda: write(conn, other, sample=s1, mv="v3", digest="A"))
    _refused(conn, source_fk, lambda: write(conn, other, sample=s1, digest="C", supersedes=back))
    _refused(conn, f"fk_{short}_supersedes_same_chain",
             lambda: write(conn, other, sample=s_other, digest="C", supersedes=back))
    # An unreferenced root, so no chain link sees these UPDATEs.
    _refused(conn, source_fk, lambda: conn.execute(text(
        f"UPDATE {table} SET user_id = :o WHERE id = :r"), {"o": other, "r": other_model}))
    _refused(conn, source_fk, lambda: conn.execute(text(
        f"UPDATE {table} SET source_wellness_sample_id = :s WHERE id = :r"),
        {"s": s_other, "r": other_model}))


def test_a062_keeps_legacy_rows_and_enforces_correction_chains(_migrated_schema: None) -> None:
    probe_db = f"perflab_a062_migration_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
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
                "VALUES ('a062@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            _recovery(conn, uid)
            _personalization(conn, uid)
            snapshot = {
                t: conn.execute(text(f"SELECT * FROM {t} ORDER BY id")).all()
                for t in ("recovery_shadow_log", "personalization_shadow_log")
            }
            conn.commit()

            command.upgrade(cfg, _HEAD)
            for table, rows in snapshot.items():
                after = conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings().all()
                assert [tuple(r[c] for c in r if c not in _PROVENANCE) for r in after] == [
                    tuple(r) for r in rows]
                assert all(r[c] is None for r in after for c in _PROVENANCE)

            other = conn.execute(text(
                "INSERT INTO users (email, hashed_password, is_active, created_at) "
                "VALUES ('a062-other@test.com', 'h', true, now()) RETURNING id"
            )).scalar_one()
            s1, s2 = _sample(conn, uid, "2026-10-01"), _sample(conn, uid, "2026-10-02")
            s_other = _sample(conn, other, "2026-10-01")
            _chain_keys_hold(conn, uid, "recovery_shadow", _recovery, s1, s2, other, s_other)
            _chain_keys_hold(conn, uid, "personalization_shadow", _personalization, s1, s2,
                             other, s_other)
            # Personalization chains are per parameter as well.
            _personalization(conn, uid, sample=s2, parameter="other_beta", digest="A")
            conn.commit()

            # Deleting a sample takes its chains with it, as ekf_shadow_log's link does.
            conn.execute(text("DELETE FROM wellness_samples WHERE id = :s"), {"s": s2})
            for table in ("recovery_shadow_log", "personalization_shadow_log"):
                assert conn.execute(text(
                    f"SELECT count(*) FROM {table} WHERE source_wellness_sample_id = :s"
                ), {"s": s2}).scalar_one() == 0
            conn.commit()

            # Linked rows refuse the downgrade, which changes nothing.
            with pytest.raises(RuntimeError, match=r"linked shadow rows \(4 in recovery_shadow_log, "
                                                   r"4 in personalization_shadow_log\)"):
                command.downgrade(cfg, _BEFORE)
            conn.rollback()
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _HEAD
            assert _has_column(conn, "recovery_shadow_log", "source_wellness_sample_id")

            # With the linked rows deleted deliberately, it runs and the legacy rows survive.
            for table in ("recovery_shadow_log", "personalization_shadow_log"):
                conn.execute(text(f"DELETE FROM {table} WHERE source_wellness_sample_id IS NOT NULL"))
            conn.commit()
            command.downgrade(cfg, _BEFORE)
            for table, rows in snapshot.items():
                assert not _has_column(conn, table, "supersedes_id")
                after = conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
                assert [tuple(r) for r in after] == [tuple(r) for r in rows]

            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{probe_db}"')
