"""Backfill standardization_rules + observation_mappings for Phase-1B coverage.

Data-only migration (no schema change). Sets ``standardization_rules`` on 18
previously-unmapped anchor benchmark definitions (only where currently NULL —
never clobbers an existing value) and inserts the corresponding capacity/
residual ``observation_mappings`` rows so the residual anchor (ADR-0034) can
fire for them. Mirrors the same additions made to
``app.scripts.seed_benchmarks`` BENCHMARKS/MAPPINGS. Grip domain, tissue-vector
targets, and validator-only defs are intentionally excluded (deferred).

Idempotent: standardization_rules update is guarded by `IS NULL`; mapping
inserts are guarded by an existence check on
(benchmark_definition_id, target_vector, target_key). A benchmark code with no
matching row in benchmark_definitions is silently skipped.

Revision ID: a016_benchmark_mapping_coverage
Revises: a015_personalization_shadow_log
Create Date: 2026-07-06
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a016_benchmark_mapping_coverage"
down_revision: str | None = "a015_personalization_shadow_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STANDARDIZATION_RULES: dict[str, dict[str, float]] = {
    "run_threshold_pace_30min_tt": {"floor": 7.5, "cap": 3.0},
    "run_long_run_decoupling": {"floor": 15.0, "cap": 2.0},
    "sprint_0_30_split": {"floor": 5.5, "cap": 3.7},
    "sprint_flying_30": {"floor": 4.0, "cap": 2.5},
    "sprint_60m_time": {"floor": 9.5, "cap": 6.4},
    "sprint_150m_time": {"floor": 24.0, "cap": 14.5},
    "sprint_300m_time": {"floor": 55.0, "cap": 32.0},
    "wl_front_squat_1rm": {"floor": 30.0, "cap": 220.0},
    "wl_technical_grade_85pct": {"floor": 0.0, "cap": 100.0},
    "wl_back_squat_1rm": {"floor": 40.0, "cap": 260.0},
    "gym_ring_support_hold": {"floor": 0.0, "cap": 60.0},
    "gym_handstand_hold": {"floor": 0.0, "cap": 120.0},
    "gym_strict_dip_max": {"floor": 0.0, "cap": 40.0},
    "mm_short_benchmark_wod": {"floor": 600.0, "cap": 180.0},
    "mm_aerobic_skill_benchmark_wod": {"floor": 1500.0, "cap": 480.0},
    "mm_row_2k": {"floor": 600.0, "cap": 360.0},
    "mm_bike_10min_output": {"floor": 80.0, "cap": 350.0},
    "mm_repeatability_test": {"floor": 0.0, "cap": 100.0},
}

# (benchmark_code, target_key, coefficient) — all target_vector="capacity",
# mapping_type="residual", intercept=0.0, config={}. Mirrors the Phase-1B
# block appended to app.scripts.seed_benchmarks.MAPPINGS.
_NEW_MAPPINGS: list[tuple[str, str, float]] = [
    ("run_threshold_pace_30min_tt", "aerobic", 0.9),
    ("run_long_run_decoupling", "aerobic", 0.6),
    ("sprint_0_30_split", "power", 0.8),
    ("sprint_0_30_split", "skill", 0.4),
    ("sprint_flying_30", "power", 0.9),
    ("sprint_60m_time", "power", 0.8),
    ("sprint_150m_time", "power", 0.6),
    ("sprint_150m_time", "glycolytic", 0.6),
    ("sprint_300m_time", "glycolytic", 0.8),
    ("sprint_300m_time", "power", 0.4),
    ("wl_front_squat_1rm", "max_strength", 0.8),
    ("wl_front_squat_1rm", "skill", 0.3),
    ("wl_technical_grade_85pct", "skill", 0.8),
    ("wl_back_squat_1rm", "max_strength", 0.85),
    ("gym_ring_support_hold", "skill", 0.6),
    ("gym_handstand_hold", "skill", 0.7),
    ("gym_strict_dip_max", "max_strength", 0.6),
    ("gym_strict_dip_max", "skill", 0.4),
    ("mm_short_benchmark_wod", "glycolytic", 0.7),
    ("mm_short_benchmark_wod", "work_capacity", 0.7),
    ("mm_aerobic_skill_benchmark_wod", "aerobic", 0.7),
    ("mm_aerobic_skill_benchmark_wod", "skill", 0.4),
    ("mm_row_2k", "aerobic", 0.8),
    ("mm_row_2k", "work_capacity", 0.6),
    ("mm_bike_10min_output", "aerobic", 0.8),
    ("mm_repeatability_test", "work_capacity", 0.7),
    ("mm_repeatability_test", "glycolytic", 0.5),
]


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

    for code, target_key, coefficient in _NEW_MAPPINGS:
        bid = id_by_code.get(code)
        if bid is None:
            continue
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM observation_mappings "
                "WHERE benchmark_definition_id = :bid "
                "AND target_vector = :tv AND target_key = :tk"
            ),
            {"bid": bid, "tv": "capacity", "tk": target_key},
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
                "tk": target_key,
                "mt": "residual",
                "coef": coefficient,
                "intercept": 0.0,
                "config": json.dumps({}),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()

    for code, target_key, _coefficient in _NEW_MAPPINGS:
        bind.execute(
            sa.text(
                "DELETE FROM observation_mappings "
                "WHERE target_vector = :tv AND target_key = :tk "
                "AND benchmark_definition_id = ("
                "  SELECT id FROM benchmark_definitions WHERE code = :code"
                ")"
            ),
            {"code": code, "tv": "capacity", "tk": target_key},
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
