import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.db import Base


class WeakPointSource(str, enum.Enum):
    SELF_REPORT = "self_report"       # User explicitly flagged this
    BENCHMARK = "benchmark"           # Detected from a periodic re-test
    INFERENCE = "inference"           # LLM / system inferred from workout logs
    PERFORMANCE_DATA = "performance_data"  # Derived statistically over time


# Canonical weak point tags — used for exercise selection biasing.
# Tags are intentionally coarse-grained so the prescriber can match them
# against Exercise.weak_point_tags without needing exact string matches.
WEAK_POINT_TAGS = [
    # Movement patterns
    "hip_hinge",
    "squat_pattern",
    "push_horizontal",
    "push_vertical",
    "pull_horizontal",
    "pull_vertical",
    "carry",
    "rotation",
    "core_stability",
    "single_leg",

    # Physical qualities
    "grip",
    "posterior_chain",
    "anterior_chain",
    "overhead_stability",
    "hip_mobility",
    "ankle_mobility",
    "thoracic_mobility",

    # Energy systems
    "aerobic_base",
    "lactate_threshold",
    "anaerobic_capacity",
    "work_capacity",

    # Sport / modality specific
    "running_economy",
    "barbell_technique",
    "gymnastics_skill",
    "olympic_lifting",
    "sled_tolerance",       # Hyrox / CrossFit
    "row_technique",
    "bike_efficiency",
]


class WeakPoint(Base):
    """
    A flagged limitation for a specific user.

    Multiple sources can flag the same tag — they are stored as separate rows
    so confidence can be tracked per source and aggregated by the prescriber.

    resolved_at is set when the system detects the weak point has been
    sufficiently addressed (e.g. benchmark re-test shows improvement,
    or user manually marks it resolved).

    The prescriber aggregates active (unresolved) weak points for a user
    and passes them as bias signals to the LLM session generator.
    """
    __tablename__ = "weak_points"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # The canonical tag — must be one of WEAK_POINT_TAGS (enforced in schema layer)
    tag = Column(String, nullable=False, index=True)

    # How this was detected
    source = Column(SAEnum(WeakPointSource), nullable=False)

    # 0.0–1.0. Higher = more confident signal.
    # Typical values by source:
    #   self_report: 0.5 (user's perception may not match reality)
    #   benchmark: 0.9 (objective measurement)
    #   inference: 0.6 (LLM-derived, plausible but not verified)
    #   performance_data: 0.75 (statistical pattern over time)
    confidence = Column(Float, default=0.5, nullable=False)

    # Optional human-readable note (e.g. "grip failed before legs on deadlift")
    note = Column(Text, nullable=True)

    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    # If source=benchmark, link to the planned session that produced this signal
    source_session_id = Column(Integer, ForeignKey("planned_sessions.id"), nullable=True)

    # Relationship
    user = relationship("User", back_populates="weak_points")

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None
