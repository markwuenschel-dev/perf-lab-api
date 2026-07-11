# app/models/user.py
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,  # ← was missing
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    # Imported only for SQLAlchemy Mapped[...] forward refs; the relationship
    # strings are resolved at runtime via SQLAlchemy's class registry.
    from app.models.athlete_state import AthleteState
    from app.models.macrocycle import Macrocycle
    from app.models.mesocycle import MesocycleBlock
    from app.models.objective import Objective
    from app.models.weak_point import WeakPoint
    from app.models.wearable_connection import WearableConnection
    from app.models.wellness import WellnessSample


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # === Relationships ===
    profile: Mapped["AthleteProfile"] = relationship(
        "AthleteProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Historical athlete states (one-to-many)
    athlete_states: Mapped[list["AthleteState"]] = relationship(
        "AthleteState",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Other relationships
    blocks: Mapped[list["MesocycleBlock"]] = relationship(
        "MesocycleBlock", back_populates="user"
    )
    weak_points: Mapped[list["WeakPoint"]] = relationship(
        "WeakPoint", back_populates="user"
    )
    wellness_samples: Mapped[list["WellnessSample"]] = relationship(
        "WellnessSample", back_populates="user", cascade="all, delete-orphan"
    )
    objectives: Mapped[list["Objective"]] = relationship(
        "Objective", back_populates="user", cascade="all, delete-orphan"
    )
    macrocycles: Mapped[list["Macrocycle"]] = relationship(
        "Macrocycle", back_populates="user", cascade="all, delete-orphan"
    )
    wearable_connections: Mapped[list["WearableConnection"]] = relationship(
        "WearableConnection", back_populates="user", cascade="all, delete-orphan"
    )


class AthleteProfile(Base):
    """One-to-one profile with baseline data"""
    __tablename__ = "athlete_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_goal: Mapped[str | None] = mapped_column(Text, nullable=True)

    experience_years: Mapped[float] = mapped_column(Float, default=0.0)
    experience_level: Mapped[str] = mapped_column(String, default="beginner")
    available_days_per_week: Mapped[int] = mapped_column(Integer, default=3)
    session_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)

    equipment: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    squat_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadlift_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    bench_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    overhead_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pullup_max_reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_5k_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    run_1p5mi_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bodyweight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # === Onboarding state machine (PDR-0010) ===
    # The app never blocks on a performance measurement; onboarding persists as
    # a resumable state machine and the seed is provisional until measured.
    onboarding_status: Mapped[str] = mapped_column(
        String,
        default="not_started",
        nullable=False,
        comment="not_started | in_progress | completed",
    )
    completed_reason: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="How onboarding ended, e.g. finished | done_for_now | skipped",
    )
    # Derived analytics rollups over `initial_seed_by_axis` (ADR-0059) — NOT a
    # parallel confidence authority. The live per-axis CapacityConfidence is the sole
    # runtime source for provisionality; these are versioned summaries for display/ops.
    initial_seed_status: Mapped[str] = mapped_column(
        String,
        default="none",
        nullable=False,
        comment="Rollup (initial_seed_status_rollup_v1): none | experience_prior_only "
        "| benchmark_seeded | mixed",
    )
    initial_seed_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Legacy scalar summary; superseded by initial_seed_by_axis"
    )

    # === Immutable per-axis seed provenance snapshot (ADR-0059) ===
    # How each capacity axis was seeded at onboarding (source / evidence_tier /
    # seed_variance). Immutable provenance — never read at runtime for current
    # provisionality (enforced by tests/test_seed_snapshot_not_runtime_read.py).
    initial_seed_by_axis: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    seed_policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    seeded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # === Wellness tracking preference (ADR-0049) ===
    # Signals the user has explicitly marked "I don't track this" — hidden from
    # the check-in and never expected. Missing-but-tracked is a gap, never imputed.
    untracked_wellness_signals: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="profile")
