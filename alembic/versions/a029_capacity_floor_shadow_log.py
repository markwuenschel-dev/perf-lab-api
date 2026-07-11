"""Create capacity_floor_shadow_log — deferred upward_lower_bound floor candidates (ADR-0058).

Additive, capture-only table. When an observation resolves an ``upward_lower_bound``
capacity_effect, the authority is real but the live floor-ratchet is not promoted
(the deployed ADR-0055 invariant stays active). One row records the resolved
authority and the *would-be* applied transition **separately**: proposed floor,
projected uplift, application-policy version, and the reason mutation was not
applied. Nothing here touches production state (``decision_impact =
"none_shadow_only"``). Promotion to live is a later, observable decision.

Revision ID: a029_capacity_floor_shadow_log
Revises: a028_obs_provenance_authority
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a029_capacity_floor_shadow_log"
down_revision: str | None = "a028_obs_provenance_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capacity_floor_shadow_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "benchmark_observation_id",
            sa.Integer(),
            sa.ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("benchmark_code", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("capacity_effect", sa.String(length=30), nullable=False),
        sa.Column("authority_policy_version", sa.String(length=40), nullable=False),
        sa.Column("authority_resolution_reason", sa.Text(), nullable=True),
        sa.Column("application_policy_version", sa.String(length=50), nullable=False),
        sa.Column("not_applied_reason", sa.String(length=60), nullable=False),
        sa.Column(
            "proposed_floor_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "projected_uplift_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("projected_uplift_total", sa.Float(), nullable=False),
        sa.Column("would_raise", sa.Boolean(), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_capacity_floor_shadow_log_user_id", "capacity_floor_shadow_log", ["user_id"]
    )
    op.create_index(
        "ix_capacity_floor_shadow_log_obs_id",
        "capacity_floor_shadow_log",
        ["benchmark_observation_id"],
    )
    op.create_index(
        "ix_capacity_floor_shadow_log_id", "capacity_floor_shadow_log", ["id"]
    )


def downgrade() -> None:
    op.drop_index("ix_capacity_floor_shadow_log_id", table_name="capacity_floor_shadow_log")
    op.drop_index("ix_capacity_floor_shadow_log_obs_id", table_name="capacity_floor_shadow_log")
    op.drop_index("ix_capacity_floor_shadow_log_user_id", table_name="capacity_floor_shadow_log")
    op.drop_table("capacity_floor_shadow_log")
