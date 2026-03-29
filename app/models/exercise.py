from sqlalchemy import (
    Column, Integer, String, Boolean, Float, ARRAY, Text
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

    # Difficulty
    skill_demand = Column(
        Float,
        default=0.5,
        comment="0–1. High = Olympic lifts, complex gymnastics. Low = machine isolation."
    )
    impact_level = Column(
        Float,
        default=0.5,
        comment="0–1. Structural impact. High = heavy running, plyometrics. Low = bike."
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
    meta = Column(JSONB, default=dict)
