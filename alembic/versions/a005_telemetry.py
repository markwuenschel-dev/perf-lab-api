"""Telemetry tables for adaptive engine research (prescription, candidates, feedback, pain, outcomes).

Revision ID: a005_telemetry
Revises: a004_wellness
Create Date: 2026-06-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a005_telemetry"
down_revision: str | None = "a004_wellness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prescription_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("planned_session_id", sa.Integer(), nullable=True),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("algorithm_version", sa.String(), nullable=False, server_default="v0"),
        sa.Column("decision_mode", sa.String(), nullable=False, server_default="adaptive"),
        sa.Column("state_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("block_context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chosen_candidate_id", sa.String(), nullable=True),
        sa.Column("chosen_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["planned_session_id"], ["planned_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prescription_decisions_athlete_id", "prescription_decisions", ["athlete_id"])

    op.create_table(
        "candidate_decision_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prescription_decision_id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("candidate_type", sa.String(), nullable=False),
        sa.Column("focus", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="generator"),
        sa.Column("score_components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("hard_failed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hard_fail_reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("soft_warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chosen", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["prescription_decision_id"], ["prescription_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidate_decision_logs_decision_id", "candidate_decision_logs", ["prescription_decision_id"])

    op.create_table(
        "session_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planned_session_id", sa.Integer(), nullable=False),
        sa.Column("completed_workout_log_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("followed_as_prescribed", sa.Boolean(), nullable=True),
        sa.Column("modified_volume", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modified_intensity", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modified_exercises", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modification_reason", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("satisfaction_score", sa.Integer(), nullable=True),
        sa.Column("perceived_fit_score", sa.Integer(), nullable=True),
        sa.Column("pain_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("soreness_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["planned_session_id"], ["planned_sessions.id"]),
        sa.ForeignKeyConstraint(["completed_workout_log_id"], ["workout_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planned_session_id"),
    )
    op.create_index("ix_session_feedback_planned_id", "session_feedback", ["planned_session_id"])

    op.create_table(
        "pain_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("tissue_axis", sa.String(), nullable=False),
        sa.Column("severity_0_10", sa.Float(), nullable=False),
        sa.Column("affected_training", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("onset", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pain_reports_athlete_id", "pain_reports", ["athlete_id"])

    op.create_table(
        "outcome_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("tissue_axis", sa.String(), nullable=True),
        sa.Column("source_table", sa.String(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outcome_events_athlete_id", "outcome_events", ["athlete_id"])


def downgrade() -> None:
    op.drop_index("ix_outcome_events_athlete_id", table_name="outcome_events")
    op.drop_table("outcome_events")
    op.drop_index("ix_pain_reports_athlete_id", table_name="pain_reports")
    op.drop_table("pain_reports")
    op.drop_index("ix_session_feedback_planned_id", table_name="session_feedback")
    op.drop_table("session_feedback")
    op.drop_index("ix_candidate_decision_logs_decision_id", table_name="candidate_decision_logs")
    op.drop_table("candidate_decision_logs")
    op.drop_index("ix_prescription_decisions_athlete_id", table_name="prescription_decisions")
    op.drop_table("prescription_decisions")
