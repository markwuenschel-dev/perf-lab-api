"""Create strength_decline_candidates — downward-decline state machine (INT-02, ADR-0066).

Additive, live table. A protocol-valid ``bidirectional_update`` observation with a
*material* downward residual opens one candidate here instead of durably regressing
canonical capacity. An active row conservatively constrains prescription; a
``confirmed`` row (independent corroboration) drives a bounded estimator update.

Idempotency: ``uq_strength_decline_trigger_axis_policy`` on
``(trigger_observation_id, capacity_axis, decline_policy_version)`` guarantees replay
of the same observation can never open a parallel candidate.

Revision ID: a032_strength_decline_candidates
Revises: a031_profile_date_of_birth
Create Date: 2026-07-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a032_strength_decline_candidates"
down_revision: str | None = "a031_profile_date_of_birth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strength_decline_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("capacity_axis", sa.String(length=30), nullable=False),
        sa.Column(
            "benchmark_definition_id",
            sa.Integer(),
            sa.ForeignKey("benchmark_definitions.id"),
            nullable=False,
        ),
        sa.Column("benchmark_code", sa.String(length=100), nullable=False),
        sa.Column(
            "trigger_observation_id",
            sa.Integer(),
            sa.ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger_assessment_occurrence_id", sa.String(length=80), nullable=True),
        sa.Column("prior_mean", sa.Float(), nullable=False),
        sa.Column("prior_variance", sa.Float(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("observation_variance", sa.Float(), nullable=False),
        sa.Column("measurement_error_threshold", sa.Float(), nullable=False),
        sa.Column("normalized_residual", sa.Float(), nullable=False),
        sa.Column("threshold_source", sa.String(length=40), nullable=False),
        sa.Column(
            "fatigue_readiness_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "confirmation_observation_id",
            sa.Integer(),
            sa.ForeignKey("benchmark_observations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("applied_posterior_mean", sa.Float(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("confirmation_deadline", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("authority_policy_version", sa.String(length=40), nullable=False),
        sa.Column("decline_policy_version", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_observation_id",
            "capacity_axis",
            "decline_policy_version",
            name="uq_strength_decline_trigger_axis_policy",
        ),
    )
    op.create_index(
        "ix_strength_decline_candidates_id", "strength_decline_candidates", ["id"]
    )
    op.create_index(
        "ix_strength_decline_candidates_user_id",
        "strength_decline_candidates",
        ["user_id"],
    )
    op.create_index(
        "ix_strength_decline_candidates_trigger_obs",
        "strength_decline_candidates",
        ["trigger_observation_id"],
    )
    op.create_index(
        "ix_strength_decline_candidates_status",
        "strength_decline_candidates",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_strength_decline_candidates_status", table_name="strength_decline_candidates"
    )
    op.drop_index(
        "ix_strength_decline_candidates_trigger_obs",
        table_name="strength_decline_candidates",
    )
    op.drop_index(
        "ix_strength_decline_candidates_user_id",
        table_name="strength_decline_candidates",
    )
    op.drop_index(
        "ix_strength_decline_candidates_id", table_name="strength_decline_candidates"
    )
    op.drop_table("strength_decline_candidates")
