"""Create dose_routing_shadow_log table (Model B per-exercise routing shadow, ADR-0054).

Additive, capture-only table: one row per ingested workout recording the raw Σφ·D routed
dose (model space) + its 0–100 compatibility-scaled control-space values + routing
provenance. No existing table is touched; nothing here affects production state
(``decision_impact = "none_shadow_only"``). Promotion to drive state is a later PR.

Revision ID: a026_dose_routing_shadow_log
Revises: a025_strength_evidence_authority
Create Date: 2026-07-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a026_dose_routing_shadow_log"
down_revision: str | None = "a025_strength_evidence_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dose_routing_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workout_log_id",
            sa.Integer(),
            sa.ForeignKey("workout_logs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("routed_at", sa.DateTime(), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("calibration_basis", sa.String(length=60), nullable=False),
        sa.Column("routing_basis", sa.String(length=40), nullable=False),
        sa.Column("n_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_resolved_phi", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_unresolved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_fatigue_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_tissue_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_struct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fatigue_compat_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tissue_compat_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("struct_compat", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compat_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("k_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "contributions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "decision_impact",
            sa.String(length=40),
            nullable=False,
            server_default="none_shadow_only",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dose_routing_shadow_log_id", "dose_routing_shadow_log", ["id"], unique=False
    )
    op.create_index(
        "ix_dose_routing_shadow_log_user_id",
        "dose_routing_shadow_log",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_dose_routing_shadow_log_workout_log_id",
        "dose_routing_shadow_log",
        ["workout_log_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dose_routing_shadow_log_workout_log_id", table_name="dose_routing_shadow_log"
    )
    op.drop_index("ix_dose_routing_shadow_log_user_id", table_name="dose_routing_shadow_log")
    op.drop_index("ix_dose_routing_shadow_log_id", table_name="dose_routing_shadow_log")
    op.drop_table("dose_routing_shadow_log")
