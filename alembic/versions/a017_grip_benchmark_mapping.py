"""Backfill standardization_rules + observation_mappings for grip benchmarks.

Data-only migration (no schema change). Grip strength is a capacity but the
canonical capacity vector has no grip axis, so grip benchmarks map WEAKLY into
``capacity.max_strength`` as partial evidence of general strength (ADR-0034
amendment 2026-07-06). Sets ``standardization_rules`` on the 6 grip anchor defs
(only where currently NULL) and inserts the corresponding capacity/residual
``observation_mappings``. Mirrors the additions to
``app.scripts.seed_benchmarks``. Benchmark ``tissue_targets`` stay metadata — no
benchmark->tissue maps are created.

Idempotent: standardization_rules update is guarded by ``IS NULL``; mapping
inserts are guarded by an existence check on
(benchmark_definition_id, target_vector, target_key). A benchmark code with no
matching row in benchmark_definitions is silently skipped.

Revision ID: a017_grip_benchmark_mapping
Revises: a016_benchmark_mapping_coverage
Create Date: 2026-07-06
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a017_grip_benchmark_mapping"
down_revision: str | None = "a016_benchmark_mapping_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STANDARDIZATION_RULES: dict[str, dict[str, float]] = {
    "grip_plate_pinch_hold": {"floor": 0.0, "cap": 60.0},
    "grip_thick_bar_hold": {"floor": 0.0, "cap": 60.0},
    "grip_rolling_handle_lift": {"floor": 20.0, "cap": 120.0},
    "grip_crush_test": {"floor": 0.0, "cap": 100.0},
    "grip_farmers_hold": {"floor": 0.0, "cap": 90.0},
    "gym_false_grip_hang": {"floor": 0.0, "cap": 60.0},
}

# (benchmark_code, coefficient) — all target_vector="capacity",
# target_key="max_strength", mapping_type="residual", intercept=0.0, config={}.
_NEW_MAPPINGS: list[tuple[str, float]] = [
    ("grip_plate_pinch_hold", 0.3),
    ("grip_thick_bar_hold", 0.3),
    ("grip_rolling_handle_lift", 0.35),
    ("grip_crush_test", 0.3),
    ("grip_farmers_hold", 0.3),
    ("gym_false_grip_hang", 0.3),
]

_TARGET_KEY = "max_strength"


def upgrade() -> None:
    bind = op.get_bind()

    for code, rules in _STANDARDIZATION_RULES.items():
        bind.execute(
            sa.text(
                "UPDATE benchmark_definitions "
                "SET standardization_rules = CAST(:rules AS JSONB) "
                "WHERE code = :code AND standardization_rules IS NULL"
            ),
            {"code": code, "rules": json.dumps(rules)},
        )

    id_by_code: dict[str, int] = dict(
        bind.execute(sa.text("SELECT code, id FROM benchmark_definitions")).all()
    )

    for code, coefficient in _NEW_MAPPINGS:
        bid = id_by_code.get(code)
        if bid is None:
            continue
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM observation_mappings "
                "WHERE benchmark_definition_id = :bid "
                "AND target_vector = :tv AND target_key = :tk"
            ),
            {"bid": bid, "tv": "capacity", "tk": _TARGET_KEY},
        ).first()
        if exists:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO observation_mappings "
                "(benchmark_definition_id, target_vector, target_key, mapping_type, "
                "coefficient, intercept, config) "
                "VALUES (:bid, :tv, :tk, :mt, :coef, :intercept, CAST(:config AS JSONB))"
            ),
            {
                "bid": bid,
                "tv": "capacity",
                "tk": _TARGET_KEY,
                "mt": "residual",
                "coef": coefficient,
                "intercept": 0.0,
                "config": json.dumps({}),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()

    for code, _coefficient in _NEW_MAPPINGS:
        bind.execute(
            sa.text(
                "DELETE FROM observation_mappings "
                "WHERE target_vector = :tv AND target_key = :tk "
                "AND benchmark_definition_id = ("
                "  SELECT id FROM benchmark_definitions WHERE code = :code"
                ")"
            ),
            {"code": code, "tv": "capacity", "tk": _TARGET_KEY},
        )

    for code in _STANDARDIZATION_RULES:
        bind.execute(
            sa.text(
                "UPDATE benchmark_definitions "
                "SET standardization_rules = NULL "
                "WHERE code = :code"
            ),
            {"code": code},
        )
