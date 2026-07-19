"""Repo-audit-swarm (2026-07-17): tighten athlete_states nullability to match the ORM model.

8 columns on AthleteState (the persistent Unified State Vector S(t)) are declared
non-Optional by the SQLAlchemy model but were created ``nullable=True`` in a000. Two
verified runtime crashes result: ``UnifiedStateVector.timestamp`` is a required field, so
a NULL timestamp fails construction on every read path; and
``state_bridge.fatigue_from_legacy`` crashed on a NULL fatigue scalar (now a typed
``IncompleteLegacyState`` instead of an incidental TypeError — see the same commit) for
any row whose ``engine_state`` is absent/unparseable. ``skill_state`` / ``s_struct_signal``
/ ``habit_strength`` are already coerced to a safe default by every ``UnifiedStateVector``
construction path today, so their drift is schema-contract only, not a verified crash —
the DB should still agree with the ORM's declared contract. See ADR-0067 for the full
rationale behind each choice below.

Backfill strategy:
  - For rows with a valid, decodable ``engine_state``, the 4 legacy fatigue mirrors are
    derived from it via a *frozen snapshot* of the live projection formula
    (``state_bridge.sync_legacy_from_vectors``), proven equivalent to the live function at
    authoring time by ``tests/test_a036_athlete_states_nullability_migration.py``. This
    migration does not import application code — migrations must stay replayable
    independent of future refactors to ``app/engine/state_bridge.py``, which is itself an
    active INT-05/INT-15 convergence point (see the state-bridge-choke-point candidate) —
    the formula below is a tested-once snapshot, not a live dependency.
  - For rows with no usable ``engine_state``, the 4 fatigue mirrors backfill to 0.0 — a
    compatibility initialization value, not recovered historical truth.
  - ``s_struct_signal``, ``habit_strength`` backfill to 0.0; ``skill_state`` to ``{}`` —
    matching the ORM model's own defaults and the values every ``UnifiedStateVector``
    construction path already substitutes for NULL today.
  - ``timestamp`` backfills to one migration-transaction-scoped UTC value.
    ``athlete_states`` has no ``created_at``/``updated_at`` columns to recover a better
    historical value from (unlike users/athlete_profiles, tightened in
    a035/AUD-C12) — this value is schema-repair time, not reconstructed historical event
    time, and is never used as a runtime fallback (that would fabricate a new "freshness"
    value on every read instead of repairing it once).

Self-healing: the backfill runs first, so the migration is safe on any existing data. An
explicit guard asserts zero remaining NULLs before constraining.

Revision ID: a036_athlete_states_notnull
Revises: a035_tighten_nullability
Create Date: 2026-07-17
"""
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a036_athlete_states_notnull"
down_revision: str | None = "a035_tighten_nullability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FATIGUE_COLUMNS = ("f_met_systemic", "f_nm_peripheral", "f_nm_central", "f_struct_damage")

# Mirrors app.domain.vectors.TissueState.KEYS at authoring time — a frozen literal, not an
# import (see module docstring). Order doesn't matter; only the set and count do.
_TISSUE_KEYS = ("shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger")


def legacy_fatigue_from_engine_state(engine_state: Any) -> dict[str, float] | None:
    """Frozen snapshot of state_bridge.sync_legacy_from_vectors's fatigue projection.

    Proven equivalent to the live function at authoring time by
    tests/test_a036_athlete_states_nullability_migration.py. Frozen (not imported) so this
    migration replays correctly even if app/engine/state_bridge.py changes later. Returns
    None if ``engine_state`` is absent or lacks a usable ``f``/``t`` vector.
    """
    if isinstance(engine_state, str):
        import json

        try:
            engine_state = json.loads(engine_state)
        except json.JSONDecodeError:
            return None
    if not isinstance(engine_state, dict):
        return None

    f_raw = engine_state.get("f")
    t_raw = engine_state.get("t")
    if not isinstance(f_raw, dict) or not isinstance(t_raw, dict):
        return None
    f: dict[str, Any] = f_raw
    t: dict[str, Any] = t_raw

    try:
        structural = float(f.get("structural", 0.0))
        tendon = float(f.get("tendon", 0.0))
        grip = float(f.get("grip", 0.0))
        metabolic = float(f.get("metabolic", 0.0))
        muscular = float(f.get("muscular", 0.0))
        cns = float(f.get("cns", 0.0))
        tissue_avg = sum(float(t.get(key, 0.0)) for key in _TISSUE_KEYS) / len(_TISSUE_KEYS)
    except (TypeError, ValueError):
        return None

    f_struct_combined = min(100.0, structural + tendon + 0.15 * grip + 0.1 * tissue_avg)
    return {
        "f_met_systemic": metabolic,
        "f_nm_peripheral": muscular,
        "f_nm_central": cns,
        "f_struct_damage": f_struct_combined,
    }


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Fatigue mirrors: derive from a valid engine_state where possible, else 0.0.
    rows = conn.execute(
        sa.text(
            "SELECT id, engine_state FROM athlete_states WHERE "
            + " OR ".join(f"{c} IS NULL" for c in _FATIGUE_COLUMNS)
        )
    ).fetchall()
    for row_id, engine_state in rows:
        derived = legacy_fatigue_from_engine_state(engine_state)
        values = derived or dict.fromkeys(_FATIGUE_COLUMNS, 0.0)
        conn.execute(
            sa.text(
                "UPDATE athlete_states SET "
                + ", ".join(f"{c} = COALESCE({c}, :{c})" for c in _FATIGUE_COLUMNS)
                + " WHERE id = :row_id"
            ),
            {**values, "row_id": row_id},
        )

    # 2. Authoritative neutral-state fields: match the ORM model's own defaults.
    op.execute("UPDATE athlete_states SET s_struct_signal = 0.0 WHERE s_struct_signal IS NULL")
    op.execute("UPDATE athlete_states SET habit_strength = 0.0 WHERE habit_strength IS NULL")
    op.execute("UPDATE athlete_states SET skill_state = '{}'::jsonb WHERE skill_state IS NULL")

    # 3. timestamp: one migration-transaction-scoped UTC value (schema-repair time, not
    #    reconstructed historical event time — see ADR-0067). No better historical source
    #    exists on this table (no created_at/updated_at columns).
    op.execute(
        "UPDATE athlete_states SET timestamp = (now() at time zone 'utc') WHERE timestamp IS NULL"
    )

    # 4. Assert the backfill actually reached every row before constraining.
    remaining = conn.execute(
        sa.text(
            "SELECT count(*) FROM athlete_states WHERE "
            + " OR ".join(
                f"{c} IS NULL"
                for c in (
                    *_FATIGUE_COLUMNS,
                    "s_struct_signal",
                    "habit_strength",
                    "skill_state",
                    "timestamp",
                )
            )
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"athlete_states nullability backfill incomplete: {remaining} row(s) still NULL"
        )

    op.alter_column("athlete_states", "timestamp", existing_type=sa.DateTime(), nullable=False)
    for column in _FATIGUE_COLUMNS:
        op.alter_column("athlete_states", column, existing_type=sa.Float(), nullable=False)
    op.alter_column("athlete_states", "s_struct_signal", existing_type=sa.Float(), nullable=False)
    op.alter_column("athlete_states", "habit_strength", existing_type=sa.Float(), nullable=False)
    op.alter_column(
        "athlete_states",
        "skill_state",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column("athlete_states", "timestamp", existing_type=sa.DateTime(), nullable=True)
    for column in _FATIGUE_COLUMNS:
        op.alter_column("athlete_states", column, existing_type=sa.Float(), nullable=True)
    op.alter_column("athlete_states", "s_struct_signal", existing_type=sa.Float(), nullable=True)
    op.alter_column("athlete_states", "habit_strength", existing_type=sa.Float(), nullable=True)
    op.alter_column(
        "athlete_states",
        "skill_state",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    )
