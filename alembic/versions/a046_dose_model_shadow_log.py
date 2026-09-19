"""Shadow log of dose model v1 computed beside production v0 (phase 8A).

One row per ingested workout: both doses, their ratio, the density and volume-set provenance
of each, the athlete's state entering the session, and the model/code versions that produced
the observation. Capture-only — ``decision_impact`` is always ``none_shadow_only`` and nothing
here is ever read back into state or a recommendation.

This is the dataset phase 8B fits against. See app/models/dose_model_shadow.py for why each
provenance column exists.

Revision ID: a046_dose_model_shadow_log
Revises: a045_session_domain_intensity
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids at or under 32 characters.
revision: str = "a046_dose_model_shadow_log"
down_revision: str | None = "a045_session_domain_intensity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dose_model_shadow_log"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workout_log_id",
            sa.Integer(),
            sa.ForeignKey("workout_logs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("v0_model_version", sa.String(length=40), nullable=False),
        sa.Column("v1_model_version", sa.String(length=40), nullable=False),
        sa.Column("state_update_model", sa.String(length=40), nullable=True),
        sa.Column("prescription_engine_version", sa.String(length=40), nullable=True),
        sa.Column("code_version", sa.String(length=64), nullable=True),
        sa.Column("modality", sa.String(length=40), nullable=False),
        sa.Column("planned_domain", sa.String(length=40), nullable=True),
        sa.Column("planned_category", sa.String(length=80), nullable=True),
        sa.Column("duration_minutes", sa.Float(), nullable=False),
        sa.Column("session_rpe", sa.Float(), nullable=False),
        sa.Column("reported_sets", sa.Float(), nullable=True),
        sa.Column("total_volume_load", sa.Float(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("n_exercises", sa.Integer(), nullable=False),
        sa.Column("n_set_rows", sa.Integer(), nullable=False),
        sa.Column("v0_total", sa.Float(), nullable=False),
        sa.Column("v1_total", sa.Float(), nullable=False),
        sa.Column("ratio_v1_v0", sa.Float(), nullable=True),
        sa.Column("v0_density_value", sa.Float(), nullable=True),
        sa.Column("v0_density_basis", sa.String(length=40), nullable=True),
        sa.Column("v1_density_value", sa.Float(), nullable=True),
        sa.Column("v1_density_basis", sa.String(length=40), nullable=True),
        sa.Column("v0_volume_sets_basis", sa.String(length=40), nullable=True),
        sa.Column("v1_volume_sets_basis", sa.String(length=40), nullable=True),
        sa.Column("v1_density_not_modelled", sa.Boolean(), nullable=False),
        sa.Column("v0_volume_used_fabricated_sets", sa.Boolean(), nullable=False),
        sa.Column("v0_dose_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("v1_dose_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("id", "user_id", "workout_log_id", "modality"):
        op.create_index(f"ix_{_TABLE}_{column}", _TABLE, [column], unique=False)


def downgrade() -> None:
    for column in ("modality", "workout_log_id", "user_id", "id"):
        op.drop_index(f"ix_{_TABLE}_{column}", table_name=_TABLE)
    op.drop_table(_TABLE)
