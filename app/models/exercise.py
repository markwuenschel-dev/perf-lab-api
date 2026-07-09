from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Exercise(Base):
    """
    Movement library entry. Seed data — not user-specific.

    The prescriber uses this table to select concrete exercises given:
      - required modality / movement pattern
      - user's available equipment
      - weak point bias tags

    is_benchmark=True marks exercises used in periodic re-test protocols
    (e.g. Back Squat 1RM, 5K run, max pull-ups).
    """
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    # Classification
    modality: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        comment="Strength | Hypertrophy | Power | Running | Conditioning | Calisthenics | Mixed"
    )
    movement_pattern: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        comment="squat | hinge | push_horizontal | push_vertical | pull_horizontal | "
                "pull_vertical | carry | run | row | bike | jump | rotation | core | mixed"
    )
    pattern_family: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
        comment="e.g. squat_family | hinge_family | press_family | pull_family | locomotion"
    )
    unilateral: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="True if single-limb emphasis"
    )
    rom_demand: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="0–1 normalized ROM requirement"
    )
    contraction_bias: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="eccentric | concentric | isometric | mixed"
    )

    # Muscles
    primary_muscles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    secondary_muscles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Equipment
    equipment_required: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        comment="Tags must match AthleteProfile.equipment. Empty = bodyweight."
    )

    # Load / prescription type
    load_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="barbell | dumbbell | bodyweight | machine | cable | "
                "kettlebell | distance | time | reps"
    )
    sport_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        comment="e.g. powerlifting | weightlifting | hyrox | crossfit | gymnastics"
    )
    scalable_by: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="load | band | elevation | tempo | duration | distance"
    )

    # Difficulty
    skill_demand: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        comment="0–1. High = Olympic lifts, complex gymnastics. Low = machine isolation."
    )
    technical_ceiling: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        comment="0–1. Movement complexity cap before regressions recommended"
    )
    impact_level: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        comment="0–1. Structural impact. High = heavy running, plyometrics. Low = bike."
    )
    recovery_cost: Mapped[float] = mapped_column(
        Float,
        default=0.5,
        comment="0–1. Expected systemic cost between sessions"
    )
    novelty_penalty: Mapped[float] = mapped_column(
        Float,
        default=0.1,
        comment="0–1. Novelty / coordination tax for dose model"
    )

    # Vector ontology (see PROJECT_AGENT_BRIEF.md)
    phi_adapt: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="φ_adapt weights"
    )
    phi_fatigue: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="φ_fatigue weights"
    )
    phi_tissue: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, comment="φ_tissue joint stress weights"
    )
    energy_mix: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        comment='{"aerobic","glycolytic","alactic"} fractions'
    )

    # Weak-point targeting tags
    # e.g. ["grip", "posterior_chain", "aerobic_base", "hip_hinge"]
    weak_point_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        comment="Used to bias exercise selection toward flagged weak points"
    )

    # Benchmark flag
    is_benchmark: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="If True, this exercise is used in periodic re-test / assessment protocols"
    )
    e1rm_benchmark_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="benchmark_definitions.code for this lift's estimated 1RM (e.g. "
                "'pl_e1rm_squat'). Set only on barbell lifts with a seeded e1RM "
                "anchor; drives write-time e1RM extraction and %e1RM prescription "
                "(ADR-0045). See app/logic/e1rm.py."
    )

    # Optional notes for the prescriber / LLM context
    coaching_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Cue notes, common errors, scaling options — fed to LLM prescriber"
    )

    # Arbitrary extra metadata (tempo, rest guidelines, etc.)
    # Optional: {"provenance_primitive_ids": ["twin_state_engine", ...]} — see training_primitives.json
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
