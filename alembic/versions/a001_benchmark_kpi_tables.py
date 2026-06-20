"""Benchmark definitions, observations, derived KPIs, observation mappings.

Revision ID: a001_benchmark_kpi
Revises:
Create Date: 2026-04-04

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a001_benchmark_kpi"
down_revision: str | None = "a000_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("metric_type", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("is_primary_anchor", sa.Boolean(), nullable=False),
        sa.Column("is_derived_only", sa.Boolean(), nullable=False),
        sa.Column("is_validator_only", sa.Boolean(), nullable=False),
        sa.Column("protocol_summary", sa.Text(), nullable=True),
        sa.Column("standardization_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("minimum_retest_interval_days", sa.Integer(), nullable=True),
        sa.Column("better_direction", sa.String(length=20), nullable=False),
        sa.Column("observation_weight", sa.Float(), nullable=False),
        sa.Column("state_targets", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("fatigue_targets", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("tissue_targets", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "NOT (is_primary_anchor AND is_derived_only)",
            name="ck_benchmark_anchor_not_derived_only",
        ),
        sa.CheckConstraint(
            "NOT (is_primary_anchor AND is_validator_only)",
            name="ck_benchmark_anchor_not_validator_only",
        ),
    )
    op.create_index(op.f("ix_benchmark_definitions_code"), "benchmark_definitions", ["code"], unique=True)
    op.create_index(op.f("ix_benchmark_definitions_domain"), "benchmark_definitions", ["domain"], unique=False)

    op.create_table(
        "benchmark_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("benchmark_definition_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=False),
        sa.Column("secondary_value", sa.Float(), nullable=True),
        sa.Column("normalized_value", sa.Float(), nullable=True),
        sa.Column("bodyweight_kg", sa.Float(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("heart_rate_avg", sa.Float(), nullable=True),
        sa.Column("heart_rate_drift_pct", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("protocol_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validity_status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["benchmark_definition_id"], ["benchmark_definitions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_benchmark_observations_user_id"), "benchmark_observations", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_benchmark_observations_benchmark_definition_id"),
        "benchmark_observations",
        ["benchmark_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_benchmark_observations_observed_at"),
        "benchmark_observations",
        ["observed_at"],
        unique=False,
    )

    op.create_table(
        "derived_metric_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("metric_type", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("formula_type", sa.String(length=50), nullable=False),
        sa.Column("formula_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("display_priority", sa.Integer(), nullable=False),
        sa.Column("is_dashboard_kpi", sa.Boolean(), nullable=False),
        sa.Column("can_affect_prescriber_rules", sa.Boolean(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_derived_metric_definitions_code"),
        "derived_metric_definitions",
        ["code"],
        unique=True,
    )
    op.create_index(
        op.f("ix_derived_metric_definitions_domain"),
        "derived_metric_definitions",
        ["domain"],
        unique=False,
    )

    op.create_table(
        "derived_metric_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("derived_metric_definition_id", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("contributing_observation_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["derived_metric_definition_id"], ["derived_metric_definitions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_derived_metric_snapshots_user_id"),
        "derived_metric_snapshots",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_derived_metric_snapshots_derived_metric_definition_id"),
        "derived_metric_snapshots",
        ["derived_metric_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_derived_metric_snapshots_computed_at"),
        "derived_metric_snapshots",
        ["computed_at"],
        unique=False,
    )

    op.create_table(
        "observation_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("benchmark_definition_id", sa.Integer(), nullable=False),
        sa.Column("target_vector", sa.String(length=20), nullable=False),
        sa.Column("target_key", sa.String(length=50), nullable=False),
        sa.Column("mapping_type", sa.String(length=50), nullable=False),
        sa.Column("coefficient", sa.Float(), nullable=False),
        sa.Column("intercept", sa.Float(), nullable=False),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["benchmark_definition_id"], ["benchmark_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_observation_mappings_benchmark_definition_id"),
        "observation_mappings",
        ["benchmark_definition_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_observation_mappings_benchmark_definition_id"), table_name="observation_mappings")
    op.drop_table("observation_mappings")

    op.drop_index(op.f("ix_derived_metric_snapshots_computed_at"), table_name="derived_metric_snapshots")
    op.drop_index(
        op.f("ix_derived_metric_snapshots_derived_metric_definition_id"),
        table_name="derived_metric_snapshots",
    )
    op.drop_index(op.f("ix_derived_metric_snapshots_user_id"), table_name="derived_metric_snapshots")
    op.drop_table("derived_metric_snapshots")

    op.drop_index(op.f("ix_derived_metric_definitions_domain"), table_name="derived_metric_definitions")
    op.drop_index(op.f("ix_derived_metric_definitions_code"), table_name="derived_metric_definitions")
    op.drop_table("derived_metric_definitions")

    op.drop_index(op.f("ix_benchmark_observations_observed_at"), table_name="benchmark_observations")
    op.drop_index(
        op.f("ix_benchmark_observations_benchmark_definition_id"),
        table_name="benchmark_observations",
    )
    op.drop_index(op.f("ix_benchmark_observations_user_id"), table_name="benchmark_observations")
    op.drop_table("benchmark_observations")

    op.drop_index(op.f("ix_benchmark_definitions_domain"), table_name="benchmark_definitions")
    op.drop_index(op.f("ix_benchmark_definitions_code"), table_name="benchmark_definitions")
    op.drop_table("benchmark_definitions")
