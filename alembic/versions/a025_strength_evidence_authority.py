"""Strength-evidence authority + provenance on benchmark_observations (ADR-0055 hotfix).

Additive columns that let the capacity-update path distinguish a protocol-grade
benchmark measurement from an opportunistic workout-derived estimate, so training
logs can never regress canonical capacity. Backfills existing rows conservatively
(workout_extraction → estimated / non-regressing / quarantined; everything else →
direct measurement / capacity-authoritative) and adds NOT VALID guard constraints.

Revision ID: a025_strength_evidence_authority
Revises: a024_exercise_e1rm_code
Create Date: 2026-07-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a025_strength_evidence_authority"
down_revision: str | None = "a024_exercise_e1rm_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "benchmark_observations"

_NEW_COLUMNS = [
    ("evidence_type", sa.String(40)),
    ("value_semantics", sa.String(20)),
    ("observation_model", sa.String(50)),
    ("model_version", sa.String(30)),
    ("affects_capacity", sa.Boolean()),
    ("can_regress_capacity", sa.Boolean()),
    ("affects_prescription", sa.Boolean()),
    ("observation_weight", sa.Float()),
    ("confidence", sa.Float()),
    ("exercise_id", sa.Integer()),
    ("workout_log_id", sa.Integer()),
    ("set_log_id", sa.Integer()),
    ("reps", sa.Integer()),
    ("load_kg", sa.Float()),
    ("rir", sa.Float()),
    ("formula", sa.String(30)),
    ("effort_fidelity", sa.String(20)),
    ("quarantined_at", sa.DateTime()),
    ("quarantine_reason", sa.String(80)),
]


def upgrade() -> None:
    for name, type_ in _NEW_COLUMNS:
        op.add_column(_T, sa.Column(name, type_, nullable=True))

    op.create_foreign_key(
        "fk_benchmark_obs_exercise", _T, "exercises", ["exercise_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_benchmark_obs_workout", _T, "workout_logs", ["workout_log_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_benchmark_obs_set", _T, "workout_set_logs", ["set_log_id"], ["id"]
    )
    op.create_index("ix_benchmark_obs_exercise_id", _T, ["exercise_id"])
    op.create_index("ix_benchmark_obs_workout_log_id", _T, ["workout_log_id"])

    # --- Backfill existing rows conservatively -------------------------------
    # Workout-derived rows lose capacity authority and are quarantined; they remain
    # for history/tracking. This is the remediation half of the hotfix.
    op.execute(
        f"""
        UPDATE {_T} SET
            evidence_type = 'estimated_from_training_set',
            value_semantics = 'estimated',
            observation_model = 'legacy_import',
            affects_capacity = false,
            can_regress_capacity = false,
            affects_prescription = false,
            observation_weight = 0.0,
            validity_status = 'quarantined',
            quarantined_at = now(),
            quarantine_reason = 'legacy_workout_extraction_capacity_authority_removed'
        WHERE source = 'workout_extraction'
        """
    )
    # Everything else predates workout extraction and is a genuine benchmark /
    # manual measurement → capacity-authoritative.
    op.execute(
        f"""
        UPDATE {_T} SET
            evidence_type = 'direct_measurement',
            value_semantics = 'measured',
            observation_model = 'benchmark_protocol',
            affects_capacity = true,
            can_regress_capacity = true,
            affects_prescription = true
        WHERE source <> 'workout_extraction'
        """
    )

    # --- Guard constraints (NOT VALID so deploy never blocks on historical dirt) ---
    for name, expr in (
        (
            "chk_workout_extraction_no_regress",
            "source <> 'workout_extraction' OR can_regress_capacity IS NOT TRUE",
        ),
        (
            "chk_workout_extraction_not_direct",
            "source <> 'workout_extraction' OR evidence_type IS DISTINCT FROM 'direct_measurement'",
        ),
        (
            "chk_observation_confidence_range",
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        ),
        (
            "chk_observation_weight_nonnegative",
            "observation_weight IS NULL OR observation_weight >= 0",
        ),
    ):
        op.execute(f"ALTER TABLE {_T} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")


def downgrade() -> None:
    for name in (
        "chk_workout_extraction_no_regress",
        "chk_workout_extraction_not_direct",
        "chk_observation_confidence_range",
        "chk_observation_weight_nonnegative",
    ):
        op.execute(f"ALTER TABLE {_T} DROP CONSTRAINT IF EXISTS {name}")
    op.drop_index("ix_benchmark_obs_workout_log_id", table_name=_T)
    op.drop_index("ix_benchmark_obs_exercise_id", table_name=_T)
    op.drop_constraint("fk_benchmark_obs_set", _T, type_="foreignkey")
    op.drop_constraint("fk_benchmark_obs_workout", _T, type_="foreignkey")
    op.drop_constraint("fk_benchmark_obs_exercise", _T, type_="foreignkey")
    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column(_T, name)
