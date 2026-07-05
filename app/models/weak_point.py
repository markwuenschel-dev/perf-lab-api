import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class WeakPointSource(str, enum.Enum):
    SELF_REPORT = "self_report"       # User explicitly flagged this
    BENCHMARK = "benchmark"           # Detected from a periodic re-test
    INFERENCE = "inference"           # LLM / system inferred from workout logs
    PERFORMANCE_DATA = "performance_data"  # Derived statistically over time


def _weak_point_source_values(enum_cls: type[WeakPointSource]) -> list[str]:
    """SAEnum values_callable: persist by member value (lowercase), matching the PG type."""
    return [str(member.value) for member in enum_cls]


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # The canonical tag — must be one of WEAK_POINT_TAGS (enforced in schema layer)
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # How this was detected. The PG enum type (migration a000) is built from the
    # member *values* (lowercase), so serialize by value — SAEnum defaults to the
    # member name, which would send "SELF_REPORT" and fail the type check.
    source: Mapped[WeakPointSource] = mapped_column(
        SAEnum(WeakPointSource, values_callable=_weak_point_source_values),
        nullable=False,
    )

    # 0.0–1.0. Higher = more confident signal.
    # Typical values by source:
    #   self_report: 0.5 (user's perception may not match reality)
    #   benchmark: 0.9 (objective measurement)
    #   inference: 0.6 (LLM-derived, plausible but not verified)
    #   performance_data: 0.75 (statistical pattern over time)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    # Optional human-readable note (e.g. "grip failed before legs on deadlift")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # If source=benchmark, link to the planned session that produced this signal
    source_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("planned_sessions.id"), nullable=True
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="weak_points")

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None
