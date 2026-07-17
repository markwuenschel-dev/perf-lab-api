"""a036: athlete_states nullability backfill + NOT NULL constraint (see ADR-0067).

1. The migration's frozen fatigue-projection formula must match the live
   ``state_bridge.sync_legacy_from_vectors`` formula (proves no drift at authoring
   time — the migration itself deliberately does not import application code, so it
   stays replayable independent of future refactors to state_bridge.py).
2. The actual migration, run against a throwaway database seeded with NULL rows (with
   and without a valid engine_state), backfills correctly and enforces NOT NULL;
   downgrade reverses it.
"""
import importlib.util
import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from app.domain.vectors import CapacityState, FatigueState, TissueState
from app.engine.state_bridge import sync_legacy_from_vectors

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "a036_athlete_states_notnull.py"
)
_spec = importlib.util.spec_from_file_location("a036_migration", _MIGRATION_PATH)
assert _spec is not None and _spec.loader is not None
_a036 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_a036)

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


def test_frozen_formula_matches_live_projector():
    """The migration's snapshot formula must currently agree with the live one."""
    f = FatigueState(
        cns=12.0, muscular=34.0, metabolic=56.0, structural=20.0, tendon=10.0, grip=40.0
    )
    t = TissueState(shoulder=5, elbow=6, wrist=7, lumbar=8, hip=9, knee=10, ankle=11, finger=12)
    live = sync_legacy_from_vectors(CapacityState(), f, t)

    engine_state = {"f": f.model_dump(), "t": t.model_dump()}
    frozen = _a036.legacy_fatigue_from_engine_state(engine_state)

    assert frozen == {
        "f_met_systemic": live["f_met_systemic"],
        "f_nm_peripheral": live["f_nm_peripheral"],
        "f_nm_central": live["f_nm_central"],
        "f_struct_damage": live["f_struct_damage"],
    }


@pytest.mark.parametrize(
    "engine_state",
    [None, "not json", {"x": {}}, {"f": "not a dict", "t": {}}],
)
def test_frozen_formula_returns_none_when_unusable(engine_state):
    assert _a036.legacy_fatigue_from_engine_state(engine_state) is None


def _current_rev(conn: Connection) -> str | None:
    return conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()


def _drop_database(name: str) -> None:
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        admin.dispose()


def test_migration_backfills_nulls_and_constrains(_migrated_schema: None) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    probe_db = f"perflab_a036_migration_{worker}"

    _drop_database(probe_db)
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{probe_db}"'))
    finally:
        admin.dispose()

    engine = create_engine(_sync_url(probe_db))
    cfg = Config("alembic.ini")
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn

            # Land everything up to (but not including) a036 first.
            command.upgrade(cfg, "a035_tighten_nullability")

            # Row A: no engine_state at all -> fatigue mirrors backfill to 0.0.
            conn.execute(
                text(
                    "INSERT INTO athlete_states "
                    "(user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, b_met_anaerobic) "
                    "VALUES (NULL, NULL, 50.0, 500.0, 50.0, 50.0)"
                )
            )

            # Row B: a valid engine_state -> fatigue mirrors derive from it.
            engine_state_json = (
                '{"version": 2, '
                '"f": {"cns": 12.0, "muscular": 34.0, "metabolic": 56.0, '
                '"structural": 20.0, "tendon": 10.0, "grip": 40.0}, '
                '"t": {"shoulder": 5, "elbow": 6, "wrist": 7, "lumbar": 8, '
                '"hip": 9, "knee": 10, "ankle": 11, "finger": 12}}'
            )
            conn.execute(
                text(
                    "INSERT INTO athlete_states "
                    "(user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, "
                    "b_met_anaerobic, engine_state) "
                    "VALUES (NULL, NULL, 50.0, 500.0, 50.0, 50.0, CAST(:eng AS jsonb))"
                ),
                {"eng": engine_state_json},
            )
            conn.commit()

            command.upgrade(cfg, "head")
            head = _current_rev(conn)
            assert head, "upgrade to head must set a revision"

            rows = conn.execute(
                text(
                    "SELECT engine_state, timestamp, f_met_systemic, f_nm_peripheral, "
                    "f_nm_central, f_struct_damage, s_struct_signal, habit_strength, "
                    "skill_state FROM athlete_states ORDER BY id"
                )
            ).fetchall()
            assert len(rows) == 2

            row_a, row_b = rows
            # Row A: no engine_state -> neutral 0.0 fallback.
            assert row_a.engine_state is None
            assert row_a.timestamp is not None
            assert row_a.f_met_systemic == 0.0
            assert row_a.f_nm_peripheral == 0.0
            assert row_a.f_nm_central == 0.0
            assert row_a.f_struct_damage == 0.0
            assert row_a.s_struct_signal == 0.0
            assert row_a.habit_strength == 0.0
            assert row_a.skill_state == {}

            # Row B: valid engine_state -> derived, not zero.
            expected = _a036.legacy_fatigue_from_engine_state(row_b.engine_state)
            assert row_b.f_met_systemic == expected["f_met_systemic"]
            assert row_b.f_nm_peripheral == expected["f_nm_peripheral"]
            assert row_b.f_nm_central == expected["f_nm_central"]
            assert row_b.f_struct_damage == expected["f_struct_damage"]
            assert row_b.f_met_systemic != 0.0

            # NOT NULL is actually enforced now.
            with pytest.raises(Exception, match="null value|not-null"):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO athlete_states "
                            "(user_id, timestamp, c_met_aerobic, c_nm_force, c_struct, "
                            "b_met_anaerobic) VALUES (NULL, NULL, 1.0, 1.0, 1.0, 1.0)"
                        )
                    )

            command.downgrade(cfg, "a035_tighten_nullability")
            command.upgrade(cfg, "head")
            assert _current_rev(conn) == head, "re-upgrade must return to the same head"

            conn.commit()
    finally:
        engine.dispose()
        _drop_database(probe_db)
