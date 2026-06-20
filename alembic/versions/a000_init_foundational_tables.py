"""Create foundational tables: users, athlete_profiles, exercises, mesocycle_blocks,
planned_sessions, workout_logs, weak_points, athlete_states.

Revision ID: a000_init
Revises: None (first migration)
Create Date: 2026-04-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a000_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True, default=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── 2. athlete_profiles ───────────────────────────────────────────────────
    op.create_table(
        "athlete_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("experience_years", sa.Float(), nullable=True, default=0.0),
        sa.Column("experience_level", sa.String(), nullable=True, default="beginner"),
        sa.Column("available_days_per_week", sa.Integer(), nullable=True, default=3),
        sa.Column("session_duration_minutes", sa.Integer(), nullable=True, default=60),
        sa.Column("equipment", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("squat_1rm", sa.Float(), nullable=True),
        sa.Column("deadlift_1rm", sa.Float(), nullable=True),
        sa.Column("bench_1rm", sa.Float(), nullable=True),
        sa.Column("overhead_1rm", sa.Float(), nullable=True),
        sa.Column("pullup_max_reps", sa.Integer(), nullable=True),
        sa.Column("run_5k_seconds", sa.Float(), nullable=True),
        sa.Column("run_1p5mi_seconds", sa.Float(), nullable=True),
        sa.Column("bodyweight_kg", sa.Float(), nullable=True),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_athlete_profiles_id", "athlete_profiles", ["id"], unique=False)

    # ── 3. exercises ──────────────────────────────────────────────────────────
    op.create_table(
        "exercises",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("modality", sa.String(), nullable=False),
        sa.Column("movement_pattern", sa.String(), nullable=False),
        sa.Column("pattern_family", sa.String(), nullable=True),
        sa.Column("unilateral", sa.Boolean(), nullable=True, default=False),
        sa.Column("rom_demand", sa.Float(), nullable=True),
        sa.Column("contraction_bias", sa.String(), nullable=True),
        sa.Column("primary_muscles", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("secondary_muscles", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("equipment_required", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("load_type", sa.String(), nullable=False),
        sa.Column("sport_domains", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("scalable_by", sa.String(), nullable=True),
        sa.Column("skill_demand", sa.Float(), nullable=True, default=0.5),
        sa.Column("technical_ceiling", sa.Float(), nullable=True, default=0.5),
        sa.Column("impact_level", sa.Float(), nullable=True, default=0.5),
        sa.Column("recovery_cost", sa.Float(), nullable=True, default=0.5),
        sa.Column("novelty_penalty", sa.Float(), nullable=True, default=0.1),
        sa.Column("phi_adapt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("phi_fatigue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("phi_tissue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("energy_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("weak_point_tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("is_benchmark", sa.Boolean(), nullable=True, default=False),
        sa.Column("coaching_notes", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_exercises_id", "exercises", ["id"], unique=False)
    op.create_index("ix_exercises_name", "exercises", ["name"], unique=True)
    op.create_index("ix_exercises_modality", "exercises", ["modality"], unique=False)
    op.create_index("ix_exercises_movement_pattern", "exercises", ["movement_pattern"], unique=False)
    op.create_index("ix_exercises_pattern_family", "exercises", ["pattern_family"], unique=False)

    # ── 4. mesocycle_blocks ───────────────────────────────────────────────────
    op.create_table(
        "mesocycle_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "goal",
            sa.Enum(
                "Strength", "Hypertrophy", "Power", "Hyrox", "CrossFit",
                "Running", "Calisthenics", "General", "Recomp",
                name="blockgoal",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "completed", "abandoned", name="blockstatus"),
            nullable=False,
            default="active",
        ),
        sa.Column("duration_weeks", sa.Integer(), nullable=False, default=8),
        sa.Column("sessions_per_week", sa.Integer(), nullable=False, default=3),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("modality_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weekly_template", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("deload_every_n_weeks", sa.Integer(), nullable=True, default=4),
        sa.Column("deload_volume_factor", sa.Float(), nullable=True, default=0.6),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mesocycle_blocks_id", "mesocycle_blocks", ["id"], unique=False)
    op.create_index("ix_mesocycle_blocks_user_id", "mesocycle_blocks", ["user_id"], unique=False)

    # ── 5. planned_sessions (without workout_log_id FK — added after workout_logs) ──
    op.create_table(
        "planned_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), sa.ForeignKey("mesocycle_blocks.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("week_number", sa.Integer(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("modality", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "completed", "skipped", "rescheduled", name="sessionstatus"),
            nullable=False,
            default="pending",
        ),
        sa.Column("prescribed_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("workout_log_id", sa.Integer(), nullable=True),  # FK added below
        sa.Column("is_deload", sa.Boolean(), nullable=True, default=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planned_sessions_id", "planned_sessions", ["id"], unique=False)
    op.create_index("ix_planned_sessions_block_id", "planned_sessions", ["block_id"], unique=False)
    op.create_index("ix_planned_sessions_user_id", "planned_sessions", ["user_id"], unique=False)
    op.create_index("ix_planned_sessions_scheduled_date", "planned_sessions", ["scheduled_date"], unique=False)

    # ── 6. workout_logs ───────────────────────────────────────────────────────
    op.create_table(
        "workout_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "planned_session_id",
            sa.Integer(),
            sa.ForeignKey("planned_sessions.id"),
            nullable=True,
        ),
        sa.Column("logged_at", sa.DateTime(), nullable=False),
        sa.Column("session_timestamp", sa.DateTime(), nullable=False),
        sa.Column("modality", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Float(), nullable=False),
        sa.Column("session_rpe", sa.Float(), nullable=False),
        sa.Column("avg_rir", sa.Float(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True, default=0.0),
        sa.Column("total_volume_load", sa.Float(), nullable=True, default=0.0),
        sa.Column("sleep_quality", sa.Float(), nullable=True, default=5.0),
        sa.Column("life_stress_inverse", sa.Float(), nullable=True, default=5.0),
        sa.Column("dose_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_benchmark", sa.Boolean(), nullable=True, default=False),
        sa.Column("benchmark_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_logs_id", "workout_logs", ["id"], unique=False)
    op.create_index("ix_workout_logs_user_id", "workout_logs", ["user_id"], unique=False)
    op.create_index("ix_workout_logs_planned_session_id", "workout_logs", ["planned_session_id"], unique=False)

    # ── 7. Deferred FK: planned_sessions.workout_log_id → workout_logs.id ────
    op.create_foreign_key(
        "fk_planned_sessions_workout_log_id",
        "planned_sessions",
        "workout_logs",
        ["workout_log_id"],
        ["id"],
    )

    # ── 8. weak_points ────────────────────────────────────────────────────────
    op.create_table(
        "weak_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "self_report", "benchmark", "inference", "performance_data",
                name="weakpointsource",
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, default=0.5),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column(
            "source_session_id",
            sa.Integer(),
            sa.ForeignKey("planned_sessions.id"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weak_points_id", "weak_points", ["id"], unique=False)
    op.create_index("ix_weak_points_user_id", "weak_points", ["user_id"], unique=False)
    op.create_index("ix_weak_points_tag", "weak_points", ["tag"], unique=False)

    # ── 9. athlete_states ─────────────────────────────────────────────────────
    op.create_table(
        "athlete_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("c_met_aerobic", sa.Float(), nullable=False),
        sa.Column("c_nm_force", sa.Float(), nullable=False),
        sa.Column("c_struct", sa.Float(), nullable=False),
        sa.Column("b_met_anaerobic", sa.Float(), nullable=False),
        sa.Column("f_met_systemic", sa.Float(), nullable=True, default=0.0),
        sa.Column("f_nm_peripheral", sa.Float(), nullable=True, default=0.0),
        sa.Column("f_nm_central", sa.Float(), nullable=True, default=0.0),
        sa.Column("f_struct_damage", sa.Float(), nullable=True, default=0.0),
        sa.Column("s_struct_signal", sa.Float(), nullable=True, default=0.0),
        sa.Column("habit_strength", sa.Float(), nullable=True, default=0.0),
        sa.Column("skill_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("engine_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_athlete_states_id", "athlete_states", ["id"], unique=False)
    op.create_index("ix_athlete_states_user_id", "athlete_states", ["user_id"], unique=False)
    op.create_index("ix_athlete_states_timestamp", "athlete_states", ["timestamp"], unique=False)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index("ix_athlete_states_timestamp", table_name="athlete_states")
    op.drop_index("ix_athlete_states_user_id", table_name="athlete_states")
    op.drop_index("ix_athlete_states_id", table_name="athlete_states")
    op.drop_table("athlete_states")

    op.drop_index("ix_weak_points_tag", table_name="weak_points")
    op.drop_index("ix_weak_points_user_id", table_name="weak_points")
    op.drop_index("ix_weak_points_id", table_name="weak_points")
    op.drop_table("weak_points")

    # Drop deferred FK first
    op.drop_constraint("fk_planned_sessions_workout_log_id", "planned_sessions", type_="foreignkey")

    op.drop_index("ix_workout_logs_planned_session_id", table_name="workout_logs")
    op.drop_index("ix_workout_logs_user_id", table_name="workout_logs")
    op.drop_index("ix_workout_logs_id", table_name="workout_logs")
    op.drop_table("workout_logs")

    op.drop_index("ix_planned_sessions_scheduled_date", table_name="planned_sessions")
    op.drop_index("ix_planned_sessions_user_id", table_name="planned_sessions")
    op.drop_index("ix_planned_sessions_block_id", table_name="planned_sessions")
    op.drop_index("ix_planned_sessions_id", table_name="planned_sessions")
    op.drop_table("planned_sessions")

    op.drop_index("ix_mesocycle_blocks_user_id", table_name="mesocycle_blocks")
    op.drop_index("ix_mesocycle_blocks_id", table_name="mesocycle_blocks")
    op.drop_table("mesocycle_blocks")

    op.drop_index("ix_exercises_pattern_family", table_name="exercises")
    op.drop_index("ix_exercises_movement_pattern", table_name="exercises")
    op.drop_index("ix_exercises_modality", table_name="exercises")
    op.drop_index("ix_exercises_name", table_name="exercises")
    op.drop_index("ix_exercises_id", table_name="exercises")
    op.drop_table("exercises")

    op.drop_index("ix_athlete_profiles_id", table_name="athlete_profiles")
    op.drop_table("athlete_profiles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")

    # Drop enum types
    sa.Enum(name="weakpointsource").drop(op.get_bind())
    sa.Enum(name="sessionstatus").drop(op.get_bind())
    sa.Enum(name="blockstatus").drop(op.get_bind())
    sa.Enum(name="blockgoal").drop(op.get_bind())
