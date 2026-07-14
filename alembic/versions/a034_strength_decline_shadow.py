"""Create strength_decline_shadow — candidate-aware prescription-basis shadow surface (INT-02, ADR-0066).

Additive, capture-only table. **Expand step (W1-C1): the table is created inert — no
runtime writer exists yet.** The writer lands in W1-C2, which persists rows only after the
production prescription commit, in an independent best-effort transaction. This migration is
deployment-enabling infrastructure; INT-02's shadow surface is not delivered until W1-C2.

Deploying this alone is a no-op: the database gains the ability to accept shadow rows and
nothing writes them. That is the point — it lets the writer be added, and reverted, without
touching schema, per the expand/contract sequence (ADR-0018 alembic-only-schema).

``resolve_prescription_basis`` (staged rollout of ``DECLINE_CANDIDATE_PRESCRIPTION_BASIS``)
today records its legacy-vs-candidate-aware comparison only as a ``logger.info`` line —
un-queryable, and missing ``absolute_delta``, ``relative_delta``, ``ceiling_semantics``, and
``policy_version`` entirely
(docs/superpowers/plans/2026-07-12-int-02-strength-decline-hysteresis.md :97, :203). This
migration creates the queryable row surface the flag promotion (off → shadow → on) must be
justified against.

``uq_strength_decline_shadow_trigger_axis_policy`` on (``trigger_observation_id``,
``capacity_axis``, ``decline_policy_version``) is the concurrency authority for the future
writer: it enforces at-most-one row per candidate lifecycle atomically, so W1-C2 can use
``INSERT ... ON CONFLICT DO NOTHING`` rather than a SELECT-before-INSERT race.

HARD CONSTRAINT: append-only counterfactual telemetry, mirroring the sibling shadow logs
(``capacity_floor_shadow_log``, ``ekf_shadow_log``, ``mpc_shadow_log``,
``recovery_shadow_log``, ``personalization_shadow_log``, ``dose_routing_shadow_log``).
``decision_impact`` is always ``"none_shadow_only"``; ``strength_decline_candidates`` alone
owns the live active/confirmed/dismissed lifecycle. Nothing here reads back into
prescription.

Revision ID: a034_strength_decline_shadow
Revises: a033_observation_applied_effect
Create Date: 2026-07-14
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a034_strength_decline_shadow"
down_revision: str | None = "a033_observation_applied_effect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strength_decline_shadow",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        # NOT NULL is load-bearing: this column is part of the unique constraint
        # below, and SQL treats NULLs as DISTINCT — one nullable column silently
        # disables the constraint entirely. Matches a032's trigger_observation_id.
        sa.Column(
            "trigger_observation_id",
            sa.Integer(),
            sa.ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("strength_decline_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("capacity_axis", sa.String(length=30), nullable=False),
        sa.Column("benchmark_code", sa.String(length=100), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("candidate_outcome", sa.String(length=20), nullable=False),
        sa.Column("prior_mean", sa.Float(), nullable=False),
        sa.Column("prior_variance", sa.Float(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("observation_variance", sa.Float(), nullable=False),
        sa.Column("threshold_source", sa.String(length=40), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("legacy_basis", sa.Float(), nullable=False),
        sa.Column("normal_basis", sa.Float(), nullable=False),
        sa.Column("candidate_aware_basis", sa.Float(), nullable=False),
        sa.Column("selected_basis", sa.Float(), nullable=False),
        sa.Column("ceiling", sa.Float(), nullable=False),
        sa.Column("absolute_delta", sa.Float(), nullable=False),
        sa.Column("relative_delta", sa.Float(), nullable=True),
        sa.Column("ceiling_semantics", sa.String(length=60), nullable=False),
        sa.Column("decline_policy_version", sa.String(length=40), nullable=False),
        sa.Column("authority_policy_version", sa.String(length=40), nullable=False),
        sa.Column("decision_impact", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_observation_id",
            "capacity_axis",
            "decline_policy_version",
            name="uq_strength_decline_shadow_trigger_axis_policy",
        ),
    )
    op.create_index(
        "ix_strength_decline_shadow_id", "strength_decline_shadow", ["id"]
    )
    op.create_index(
        "ix_strength_decline_shadow_user_id", "strength_decline_shadow", ["user_id"]
    )
    op.create_index(
        "ix_strength_decline_shadow_trigger_obs",
        "strength_decline_shadow",
        ["trigger_observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_strength_decline_shadow_trigger_obs", table_name="strength_decline_shadow"
    )
    op.drop_index(
        "ix_strength_decline_shadow_user_id", table_name="strength_decline_shadow"
    )
    op.drop_index("ix_strength_decline_shadow_id", table_name="strength_decline_shadow")
    op.drop_table("strength_decline_shadow")
