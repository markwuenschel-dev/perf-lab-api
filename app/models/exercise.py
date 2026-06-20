from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

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

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)

    # Classification
    modality = Column(
        String,
        nullable=False,
        index=True,
        comment="Strength | Hypertrophy | Power | Running | Conditioning | Calisthenics | Mixed"
    )
    movement_pattern = Column(
        String,
        nullable=False,
        index=True,
        comment="squat | hinge | push_horizontal | push_vertical | pull_horizontal | "
                "pull_vertical | carry | run | row | bike | jump | rotation | core | mixed"
    )
    pattern_family = Column(
        String,
        nullable=True,
        index=True,
        comment="e.g. squat_family | hinge_family | press_family | pull_family | locomotion"
    )
    unilateral = Column(Boolean, default=False, comment="True if single-limb emphasis")
    rom_demand = Column(Float, nullable=True, comment="0–1 normalized ROM requirement")
    contraction_bias = Column(
        String,
        nullable=True,
        comment="eccentric | concentric | isometric | mixed"
    )

    # Muscles
    primary_muscles = Column(ARRAY(String), default=list)
    secondary_muscles = Column(ARRAY(String), default=list)

    # Equipment
    equipment_required = Column(
        ARRAY(String),
        default=list,
        comment="Tags must match AthleteProfile.equipment. Empty = bodyweight."
    )

    # Load / prescription type
    load_type = Column(
        String,
        nullable=False,
        comment="barbell | dumbbell | bodyweight | machine | cable | "
                "kettlebell | distance | time | reps"
    )
    sport_domains = Column(
        ARRAY(String),
        default=list,
        comment="e.g. powerlifting | weightlifting | hyrox | crossfit | gymnastics"
    )
    scalable_by = Column(
        String,
        nullable=True,
        comment="load | band | elevation | tempo | duration | distance"
    )

    # Difficulty
    skill_demand = Column(
        Float,
        default=0.5,
        comment="0–1. High = Olympic lifts, complex gymnastics. Low = machine isolation."
    )
    technical_ceiling = Column(
        Float,
        default=0.5,
        comment="0–1. Movement complexity cap before regressions recommended"
    )
    impact_level = Column(
        Float,
        default=0.5,
        comment="0–1. Structural impact. High = heavy running, plyometrics. Low = bike."
    )
    recovery_cost = Column(
        Float,
        default=0.5,
        comment="0–1. Expected systemic cost between sessions"
    )
    novelty_penalty = Column(
        Float,
        default=0.1,
        comment="0–1. Novelty / coordination tax for dose model"
    )

    # Vector ontology (see PROJECT_AGENT_BRIEF.md)
    phi_adapt = Column(JSONB, default=dict, comment="φ_adapt weights")
    phi_fatigue = Column(JSONB, default=dict, comment="φ_fatigue weights")
    phi_tissue = Column(JSONB, default=dict, comment="φ_tissue joint stress weights")
    energy_mix = Column(
        JSONB,
        default=dict,
        comment='{"aerobic","glycolytic","alactic"} fractions'
    )

    # Weak-point targeting tags
    # e.g. ["grip", "posterior_chain", "aerobic_base", "hip_hinge"]
    weak_point_tags = Column(
        ARRAY(String),
        default=list,
        comment="Used to bias exercise selection toward flagged weak points"
    )

    # Benchmark flag
    is_benchmark = Column(
        Boolean,
        default=False,
        comment="If True, this exercise is used in periodic re-test / assessment protocols"
    )

    # Optional notes for the prescriber / LLM context
    coaching_notes = Column(
        Text,
        nullable=True,
        comment="Cue notes, common errors, scaling options — fed to LLM prescriber"
    )

    # Arbitrary extra metadata (tempo, rest guidelines, etc.)
    # Optional: {"provenance_primitive_ids": ["twin_state_engine", ...]} — see training_primitives.json
    meta = Column(JSONB, default=dict)
